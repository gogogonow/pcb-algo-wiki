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
    rf_inflate_um: int = 100  # extra padding around RF inflated bbox


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


def _build_grid(
    *,
    ir: SolverIR,
    artifact: FrontendArtifact,
    geom: GeometryIR,
    cfg: AstarConfig,
    skip_edge_id: str,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], int, int, int, int]:
    """Return (obstacles, near_rf, ix0, iy0, ix1, iy1) in cell-grid units."""
    bx0 = mm_to_um(float(ir.board.origin.x))
    by0 = mm_to_um(float(ir.board.origin.y))
    bx1 = bx0 + mm_to_um(float(ir.board.width))
    by1 = by0 + mm_to_um(float(ir.board.height))
    step = cfg.grid_step_um
    ix0, iy0 = bx0 // step, by0 // step
    ix1, iy1 = bx1 // step, by1 // step

    obstacles: set[tuple[int, int]] = set()
    near_rf: set[tuple[int, int]] = set()

    # Footprint bboxes from FrontendArtifact.
    for comp in artifact.components.values():
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
    for placement in geom.placements.values():
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

    # Inflated RF route bboxes.
    clearance_um = mm_to_um(float(ir.clearance))
    for edge_id, route in geom.routes.items():
        if edge_id == skip_edge_id:
            continue
        if route.routing_class is RoutingClass.FLEXIBLE_PATH:
            continue
        half_w = mm_to_um(float(route.width)) // 2
        infl = half_w + clearance_um + cfg.rf_inflate_um
        xs = [mm_to_um(float(p.x)) for p in route.points]
        ys = [mm_to_um(float(p.y)) for p in route.points]
        bx_lo = min(xs) - infl
        by_lo = min(ys) - infl
        bx_hi = max(xs) + infl
        by_hi = max(ys) + infl
        for cell in _cells_in_bbox(bx_lo, by_lo, bx_hi, by_hi, step):
            obstacles.add(cell)
        # Mark a second ring as "near RF" for soft cost.
        near_lo_x = bx_lo - cfg.grid_step_um
        near_lo_y = by_lo - cfg.grid_step_um
        near_hi_x = bx_hi + cfg.grid_step_um
        near_hi_y = by_hi + cfg.grid_step_um
        for cell in _cells_in_bbox(near_lo_x, near_lo_y, near_hi_x, near_hi_y, step):
            if cell not in obstacles:
                near_rf.add(cell)

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

    # Allow start/goal even if they fall on a footprint cell (endpoints sit on
    # device pads by construction). Also free a 3x3 neighbourhood so the path
    # can actually exit the pad's own bbox.
    free = set()
    for ax, ay in (start, goal):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                free.add((ax + dx, ay + dy))
    obstacles = obstacles - free

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
        obstacles, near_rf, ix0, iy0, ix1, iy1 = _build_grid(
            ir=ir, artifact=artifact, geom=geom, cfg=cfg, skip_edge_id=edge_id
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
