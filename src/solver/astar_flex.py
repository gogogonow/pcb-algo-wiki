"""M5 — Phase 3 A* router for ``flexible_path`` edges.

For every edge in the SolverIR with ``routing_class == FLEXIBLE_PATH``
(after the CP-SAT solve placed the endpoints), this module rasterises the
board into a uniform grid, marks footprint bboxes plus the inflated bbox
of every already-routed RF edge as obstacles, and runs A* with a Manhattan
heuristic to produce a polyline. The result is merged back into the
:class:`GeometryIR` returned by M4 ``extract_geometry``.

The PA reference case has zero ``flexible_path`` edges, so this module is a
no-op there. Test coverage is provided via a synthetic SolverIR fixture.

Failure mode: when no path is found (e.g. completely walled off endpoint),
the router falls back to a direct (start, end) two-point polyline so the
final SVG / GeometryIR remains well-formed; callers can detect this via
:attr:`AstarReport.failed_edges`.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from frontend.models import FrontendArtifact
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverIR
from schema.v6_ir import Point

from .units import mm_to_um, um_to_mm


@dataclass(frozen=True)
class AstarConfig:
    grid_step_um: int = 200  # 0.2 mm; PA board is 40×100 mm => 200×500 cells
    turn_cost: float = 0.5
    near_rf_cost: float = 0.3
    rf_inflate_um: int = (
        300  # extra padding around RF route buffers (~0.3mm aesthetic margin)
    )


@dataclass(frozen=True)
class PinEscapeInfo:
    """Geometric info needed to carve a pin-area escape window into the
    obstacle grid for an A* flex route endpoint.

    * ``pin_xy_um``: absolute pin centre in microns (matches one of the flex
      route polyline endpoints).
    * ``escape_dir``: unit direction the pin's "natural exit" points to, in
      grid-aligned form (one of ``(1,0)``, ``(-1,0)``, ``(0,1)``, ``(0,-1)``).
      Used to extend the window outward beyond the pad bbox; this guarantees
      the A* path can step away from the pin even if the pad envelope sits
      flush against an obstacle.
    * ``pad_w_um`` / ``pad_l_um``: pad bounding-box dimensions in microns,
      AFTER applying the component rotation.  Used as the base size of the
      carved window so we expose the pad itself but no more.
    """

    pin_xy_um: tuple[int, int]
    escape_dir: tuple[int, int]
    pad_w_um: int
    pad_l_um: int


def _escape_direction(
    local_x: float, local_y: float, rotation_deg: float
) -> tuple[int, int]:
    """Return the grid-aligned outward direction for a pin at component-local
    (local_x, local_y), after rotating the component by ``rotation_deg``.

    The chosen direction is the rotated axis (±X or ±Y) whose absolute local
    coordinate is largest — i.e., the side of the component the pin sits
    closest to.
    """
    if abs(local_x) >= abs(local_y):
        # Pin is on +X or -X side in local frame
        local_dir = (1 if local_x >= 0 else -1, 0)
    else:
        local_dir = (0, 1 if local_y >= 0 else -1)
    # Rotate by rotation_deg (snapped to {0, 90, 180, 270, -90}).
    import math as _math

    th = _math.radians(rotation_deg)
    cos_t = round(_math.cos(th))
    sin_t = round(_math.sin(th))
    rx = local_dir[0] * cos_t - local_dir[1] * sin_t
    ry = local_dir[0] * sin_t + local_dir[1] * cos_t
    return (int(rx), int(ry))


def _pin_window_cells(
    info: PinEscapeInfo, clearance_um: int, step_um: int
) -> set[tuple[int, int]]:
    """Cells inside the pad-area escape window for one pin.

    The window covers:

      * the pad's own bbox + ``clearance`` on every side (so the path can
        reach the pin centre without colliding with the pad envelope), and
      * an extension by ``step_um * 3`` cells in ``escape_dir`` (so the path
        has room to step away from the pad before being constrained by
        component-body cells / RF route halos).

    Component-body cells (the area between pads on an RLC, or the central
    region on an IC) are NOT carved out — those remain hard obstacles, so
    A* paths cannot walk through the component.  If ``escape_dir`` is
    ``(0,0)`` we fall back to an omnidirectional square (used by minimal
    test fixtures without pad metadata).
    """
    px, py = info.pin_xy_um
    half_w = info.pad_w_um // 2 + clearance_um
    half_l = info.pad_l_um // 2 + clearance_um
    dx, dy = info.escape_dir
    ext = step_um * 3  # how far past the pad bbox to keep clear

    if dx == 0 and dy == 0:
        half = max(half_w, half_l) + step_um
        x_lo, x_hi = px - half, px + half
        y_lo, y_hi = py - half, py + half
    else:
        x_lo = px - half_w - (ext if dx < 0 else 0)
        x_hi = px + half_w + (ext if dx > 0 else 0)
        y_lo = py - half_l - (ext if dy < 0 else 0)
        y_hi = py + half_l + (ext if dy > 0 else 0)

    out: set[tuple[int, int]] = set()
    for ix in range(x_lo // step_um, x_hi // step_um + 1):
        for iy in range(y_lo // step_um, y_hi // step_um + 1):
            out.add((ix, iy))
    return out


@dataclass
class AstarReport:
    routed_edges: list[str] = field(default_factory=list)
    failed_edges: list[str] = field(default_factory=list)


def _cells_in_bbox(
    bx0: int, by0: int, bx1: int, by1: int, step: int
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cx0, cy0 = bx0 // step, by0 // step
    cx1, cy1 = bx1 // step, by1 // step
    for ix in range(cx0, cx1 + 1):
        for iy in range(cy0, cy1 + 1):
            out.append((ix, iy))
    return out


def _cells_in_segment_buffer(
    x1: int, y1: int, x2: int, y2: int, infl: int, step: int
) -> list[tuple[int, int]]:
    """Return grid cells whose centres fall within *infl* of the segment (x1,y1)→(x2,y2).

    Unlike the axis-aligned bounding-box approach this correctly handles
    diagonal segments: it first computes the AABB of the fattened segment then
    filters each cell by perpendicular (point-to-segment) distance.  This
    avoids false obstacles in the "corner triangles" of diagonal routes.
    """
    bx_lo = min(x1, x2) - infl
    by_lo = min(y1, y2) - infl
    bx_hi = max(x1, x2) + infl
    by_hi = max(y1, y2) + infl
    infl_sq = infl * infl
    dx, dy = x2 - x1, y2 - y1
    len_sq = dx * dx + dy * dy
    out: list[tuple[int, int]] = []
    for ix in range(bx_lo // step, bx_hi // step + 1):
        for iy in range(by_lo // step, by_hi // step + 1):
            cx = ix * step + step // 2
            cy = iy * step + step // 2
            if len_sq == 0:
                dist_sq = (cx - x1) ** 2 + (cy - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((cx - x1) * dx + (cy - y1) * dy) / len_sq))
                px = int(x1 + t * dx)
                py = int(y1 + t * dy)
                dist_sq = (cx - px) ** 2 + (cy - py) ** 2
            if dist_sq <= infl_sq:
                out.append((ix, iy))
    return out


def _build_grid(
    *,
    ir: SolverIR,
    artifact: FrontendArtifact,
    geom: GeometryIR,
    cfg: AstarConfig,
    skip_edge_id: str,
    endpoint_pins: tuple[PinEscapeInfo, ...] = (),
    routed_flex_ids: frozenset[str] | None = None,
    endpoint_locs_um: tuple[tuple[int, int], ...] = (),
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], int, int, int, int]:
    """Return (obstacles, near_rf, ix0, iy0, ix1, iy1) in cell-grid units.

    ``endpoint_pins`` describes each endpoint pin of the current flex edge,
    used to carve a rectangular escape window through the obstacle grid
    (component footprints are now ALWAYS treated as hard obstacles — the
    only way in/out of an endpoint component is through its pin's natural
    escape window).

    ``routed_flex_ids`` lists flex-edge IDs that have already been successfully
    routed.  Those polylines (present in ``geom.routes``) are treated as hard
    obstacles so subsequent flex routes don't cross them.  Unrouted seed entries
    (2-point placeholders) are always skipped.

    ``endpoint_locs_um`` lists (x_um, y_um) locations of the current flex
    edge's endpoints in microns.  Route obstacle cells within the pad_buffer_um
    radius of these points are removed from the obstacle set so that the A*
    path can reach/leave pads that sit on or very close to an existing RF route
    (e.g. a power-bus test-point pad that coincides with the bus start point).
    """
    bx0 = mm_to_um(float(ir.board.origin.x))
    by0 = mm_to_um(float(ir.board.origin.y))
    bx1 = bx0 + mm_to_um(float(ir.board.width))
    by1 = by0 + mm_to_um(float(ir.board.height))
    step = cfg.grid_step_um
    ix0, iy0 = bx0 // step, by0 // step
    ix1, iy1 = bx1 // step, by1 // step

    obstacles: set[tuple[int, int]] = set()
    near_rf: set[tuple[int, int]] = set()

    # Footprint bboxes from FrontendArtifact — ALL components are hard
    # obstacles, including endpoint components.  Pin escape windows below
    # carve back the cells needed for the path to exit / enter pads.
    for comp_name, comp in artifact.components.items():
        if comp.bbox is None:
            continue
        bx_lo = mm_to_um(float(comp.bbox.min_x))
        by_lo = mm_to_um(float(comp.bbox.min_y))
        bx_hi = mm_to_um(float(comp.bbox.max_x))
        by_hi = mm_to_um(float(comp.bbox.max_y))
        for cell in _cells_in_bbox(bx_lo, by_lo, bx_hi, by_hi, step):
            obstacles.add(cell)

    # Pad envelopes from GeometryIR placements (UV-adhered + floating components
    # have no FrontendArtifact bbox, so we derive bbox from pad points + buffer).
    pad_buffer_um = mm_to_um(0.6)
    for comp_name, placement in geom.placements.items():
        if not placement.pads:
            continue
        xs = [mm_to_um(float(p.point.x)) for p in placement.pads]
        ys = [mm_to_um(float(p.point.y)) for p in placement.pads]
        bx_lo = min(xs) - pad_buffer_um
        by_lo = min(ys) - pad_buffer_um
        bx_hi = max(xs) + pad_buffer_um
        by_hi = max(ys) + pad_buffer_um
        for cell in _cells_in_bbox(bx_lo, by_lo, bx_hi, by_hi, step):
            obstacles.add(cell)

    # Inflated RF route obstacles — computed per-segment so that L/V-shaped
    # routes don't produce false obstacles in their concave interior.
    # Already-routed flex paths (in routed_flex_ids) are also included so that
    # subsequent flex routes don't cross them.  Unrouted flex seeds are skipped.
    _routed_flex = routed_flex_ids or frozenset()
    clearance_um = mm_to_um(float(ir.clearance))
    # Compute set of (x_um, y_um) route endpoint locations that coincide with
    # the current flex edge's endpoints (within pad_buffer).  Routes that start
    # or end within this proximity share the same pad and should not be treated
    # as obstacles at that shared endpoint location.
    _ep_locs_um = endpoint_locs_um or ()
    ep_coincidence_radius = mm_to_um(0.6)
    ep_coincidence_sq = ep_coincidence_radius * ep_coincidence_radius

    for edge_id, route in geom.routes.items():
        if edge_id == skip_edge_id:
            continue
        if route.routing_class is RoutingClass.FLEXIBLE_PATH:
            if edge_id not in _routed_flex:
                continue  # unrouted seed — skip
        half_w = mm_to_um(float(route.width)) // 2
        infl = half_w + clearance_um + cfg.rf_inflate_um
        pts = [(mm_to_um(float(p.x)), mm_to_um(float(p.y))) for p in route.points]

        # Check whether any of this route's endpoints coincide with a flex endpoint.
        # If so, the route shares the pad — skip its obstacle within that radius.
        # Skip radius must clear the route's full halo so the flex path can
        # approach the shared pad without being blocked by the route's own
        # buffer around the joint.
        coincident_eps: list[tuple[int, int]] = []
        for rx, ry in (pts[0], pts[-1]):
            for ex, ey in _ep_locs_um:
                if (rx - ex) ** 2 + (ry - ey) ** 2 <= ep_coincidence_sq:
                    coincident_eps.append((ex, ey))
                    break
        # Use max(default 0.6mm, route halo + step) so wide RF buses get a
        # large enough hole punched at their shared endpoint.
        skip_radius = max(ep_coincidence_radius, infl + step)
        skip_radius_sq = skip_radius * skip_radius

        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            seg_cells = _cells_in_segment_buffer(x1, y1, x2, y2, infl, step)
            for cell in seg_cells:
                # Skip cells that are within skip_radius of a coincident endpoint
                # (the pad is shared; no DRC violation connecting there).
                if coincident_eps:
                    cx = cell[0] * step + step // 2
                    cy = cell[1] * step + step // 2
                    skip_cell = False
                    for ex, ey in coincident_eps:
                        if (cx - ex) ** 2 + (cy - ey) ** 2 <= skip_radius_sq:
                            skip_cell = True
                            break
                    if skip_cell:
                        continue
                obstacles.add(cell)
            # Mark a one-cell ring as "near RF" for soft cost using bbox.
            near_infl = infl + step
            bx_lo = min(x1, x2) - near_infl
            by_lo = min(y1, y2) - near_infl
            bx_hi = max(x1, x2) + near_infl
            by_hi = max(y1, y2) + near_infl
            for cell in _cells_in_bbox(bx_lo, by_lo, bx_hi, by_hi, step):
                if cell not in obstacles:
                    near_rf.add(cell)

    # Finally — carve out pin escape windows for each endpoint pin of the
    # current flex edge.  Done AFTER obstacle accumulation so windows always
    # win against any earlier additions (component body, pad envelope, etc.).
    for pin_info in endpoint_pins:
        for cell in _pin_window_cells(pin_info, clearance_um, step):
            obstacles.discard(cell)

    return obstacles, near_rf, ix0, iy0, ix1, iy1


def _xy_to_cell(x_um: int, y_um: int, step: int) -> tuple[int, int]:
    return x_um // step, y_um // step


def _astar_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    obstacles: set[tuple[int, int]],
    near_rf: set[tuple[int, int]],
    ix0: int,
    iy0: int,
    ix1: int,
    iy1: int,
    cfg: AstarConfig,
) -> list[tuple[int, int]] | None:
    if start == goal:
        return [start]

    # The pin escape window carved by ``_build_grid`` is the controlled way
    # to exit/enter an endpoint pad — do NOT free a wide radius here (that
    # would undermine the window and let the path walk through the component
    # body).  Only force start/goal cells themselves to be passable.
    obstacles = obstacles - {start, goal}

    def heuristic(cell: tuple[int, int]) -> float:
        return float(abs(cell[0] - goal[0]) + abs(cell[1] - goal[1]))

    open_heap: list[tuple[float, int, tuple[int, int], tuple[int, int] | None]] = []
    counter = 0
    heapq.heappush(open_heap, (heuristic(start), counter, start, None))
    came_from: dict[tuple[int, int], tuple[tuple[int, int], tuple[int, int] | None]] = (
        {}
    )
    g_score: dict[tuple[int, int], float] = {start: 0.0}

    while open_heap:
        _, _, current, prev = heapq.heappop(open_heap)
        if current == goal:
            # Reconstruct.
            path = [current]
            node = current
            while node in came_from:
                parent, _ = came_from[node]
                path.append(parent)
                node = parent
            path.reverse()
            return path
        cx, cy = current
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (cx + dx, cy + dy)
            if nb[0] < ix0 or nb[0] > ix1 or nb[1] < iy0 or nb[1] > iy1:
                continue
            if nb in obstacles:
                continue
            step_cost = 1.0
            if prev is not None:
                pdx = current[0] - prev[0]
                pdy = current[1] - prev[1]
                if (pdx, pdy) != (dx, dy):
                    step_cost += cfg.turn_cost
            if nb in near_rf:
                step_cost += cfg.near_rf_cost
            tentative = g_score[current] + step_cost
            if tentative < g_score.get(nb, float("inf")):
                g_score[nb] = tentative
                came_from[nb] = (current, (dx, dy))
                counter += 1
                heapq.heappush(
                    open_heap,
                    (tentative + heuristic(nb), counter, nb, (dx, dy)),
                )

    return None


def _compress_polyline(cells: list[tuple[int, int]], step: int) -> tuple[Point, ...]:
    """Drop intermediate collinear cells so we emit minimum-segment polylines."""
    if not cells:
        return ()
    pts_um = [(c[0] * step, c[1] * step) for c in cells]
    out_um = [pts_um[0]]
    for i in range(1, len(pts_um) - 1):
        prev = out_um[-1]
        cur = pts_um[i]
        nxt = pts_um[i + 1]
        if (cur[0] - prev[0], cur[1] - prev[1]) == (nxt[0] - cur[0], nxt[1] - cur[1]):
            continue
        out_um.append(cur)
    out_um.append(pts_um[-1])
    return tuple(Point(x=um_to_mm(x), y=um_to_mm(y)) for x, y in out_um)


def route_flexible_paths(
    *,
    ir: SolverIR,
    artifact: FrontendArtifact,
    geom: GeometryIR,
    config: AstarConfig | None = None,
) -> tuple[GeometryIR, AstarReport]:
    """Replace flexible_path routes in ``geom`` with A*-resolved polylines.

    Returns a new :class:`GeometryIR` with the routes substituted (other
    fields are preserved). A failure to find a path collapses to a direct
    two-point polyline and is recorded in :class:`AstarReport.failed_edges`.
    """
    cfg = config or AstarConfig()
    flex_edges = [
        eid
        for eid, e in ir.edges.items()
        if e.routing_class is RoutingClass.FLEXIBLE_PATH
    ]
    if not flex_edges:
        return geom, AstarReport()

    new_routes = dict(geom.routes)
    report = AstarReport()
    for edge_id in flex_edges:
        original = geom.routes.get(edge_id)
        if original is None:
            continue
        start_pt = original.points[0]
        end_pt = original.points[-1]
        # Build per-endpoint pin escape windows.  Component bodies remain
        # hard obstacles; only the pin's "natural exit side" is carved open.
        # When pad metadata is missing (e.g. minimal test fixtures or floating
        # components without orientation), fall back to an omnidirectional
        # square window centred on the endpoint so the path can still exit.
        edge_ir = ir.edges.get(edge_id)
        endpoint_pins: list[PinEscapeInfo] = []
        DEFAULT_PAD_UM = mm_to_um(0.6)
        for endpoint_id, pt in zip(
            edge_ir.endpoints if edge_ir else (), (start_pt, end_pt)
        ):
            escape: tuple[int, int] = (0, 0)
            pad_w_um = DEFAULT_PAD_UM
            pad_l_um = DEFAULT_PAD_UM
            if "." in endpoint_id:
                comp_name, pin_name = endpoint_id.split(".", 1)
                comp = artifact.components.get(comp_name)
                placement = geom.placements.get(comp_name)
                if comp is not None:
                    pad = next((p for p in comp.pads if p.pin == pin_name), None)
                    if pad is not None and pad.pad_width and pad.pad_length:
                        rot = float(placement.rotation_deg) if placement else 0.0
                        if abs(pad.local_x) > 1e-9 or abs(pad.local_y) > 1e-9:
                            escape = _escape_direction(pad.local_x, pad.local_y, rot)
                        # Pad bbox dims swap when rotated 90/270.
                        import math as _math

                        cos_t = round(_math.cos(_math.radians(rot)))
                        if cos_t == 0:  # 90 or 270 degrees
                            pad_w_um = mm_to_um(float(pad.pad_length))
                            pad_l_um = mm_to_um(float(pad.pad_width))
                        else:
                            pad_w_um = mm_to_um(float(pad.pad_width))
                            pad_l_um = mm_to_um(float(pad.pad_length))
            endpoint_pins.append(
                PinEscapeInfo(
                    pin_xy_um=(mm_to_um(float(pt.x)), mm_to_um(float(pt.y))),
                    escape_dir=escape,
                    pad_w_um=pad_w_um,
                    pad_l_um=pad_l_um,
                )
            )
        # Build obstacles from current_geom (updated with already-routed flex
        # paths as hard obstacles to prevent route-route crossings).
        current_geom = GeometryIR(
            project=geom.project,
            board=geom.board,
            placements=geom.placements,
            routes=new_routes,
            nodes=geom.nodes,
            solve_status=geom.solve_status,
            solve_wall_seconds=geom.solve_wall_seconds,
            objective_value=geom.objective_value,
        )
        # Endpoint locations in µm for obstacle clearing near terminal pads.
        ep_locs = (
            (mm_to_um(float(start_pt.x)), mm_to_um(float(start_pt.y))),
            (mm_to_um(float(end_pt.x)), mm_to_um(float(end_pt.y))),
        )
        obstacles, near_rf, ix0, iy0, ix1, iy1 = _build_grid(
            ir=ir,
            artifact=artifact,
            geom=current_geom,
            cfg=cfg,
            skip_edge_id=edge_id,
            endpoint_pins=tuple(endpoint_pins),
            routed_flex_ids=frozenset(report.routed_edges),
            endpoint_locs_um=ep_locs,
        )
        start_cell = _xy_to_cell(
            mm_to_um(float(start_pt.x)), mm_to_um(float(start_pt.y)), cfg.grid_step_um
        )
        goal_cell = _xy_to_cell(
            mm_to_um(float(end_pt.x)), mm_to_um(float(end_pt.y)), cfg.grid_step_um
        )
        cells = _astar_path(
            start_cell, goal_cell, obstacles, near_rf, ix0, iy0, ix1, iy1, cfg
        )
        if cells is None:
            report.failed_edges.append(edge_id)
            continue
        polyline = _compress_polyline(cells, cfg.grid_step_um)
        if not polyline:
            report.failed_edges.append(edge_id)
            continue
        # Snap first/last to the exact endpoint coords so we don't drift
        # away by half a grid step.
        polyline = (start_pt,) + polyline[1:-1] + (end_pt,)
        new_routes[edge_id] = RoutePolyline(
            edge_id=edge_id,
            routing_class=original.routing_class,
            width=original.width,
            points=polyline,
        )
        report.routed_edges.append(edge_id)

    return (
        GeometryIR(
            project=geom.project,
            board=geom.board,
            placements=geom.placements,
            routes=new_routes,
            nodes=geom.nodes,
            solve_status=geom.solve_status,
            solve_wall_seconds=geom.solve_wall_seconds,
            objective_value=geom.objective_value,
        ),
        report,
    )


__all__ = [
    "AstarConfig",
    "AstarReport",
    "route_flexible_paths",
]
