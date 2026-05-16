"""Phase B — UV component adhesion.

For every UV component:

* Enumerate routed microstrip edges in the UV's ``reference_net`` and
  search for a perpendicular slot (left/right of the trace, offset by
  half_width + footprint/2 + clearance) that does not collide with the
  board frame, previously placed components, or other routed traces
  (see ``uv_slot_search``). This is the **general** path used in PA
  and other case.
* If no feasible slot exists (no host edge in net, or all blocked) fall
  back to the legacy anchor-pin-on-routed-endpoint derivation.
* Finally, fall back to seed placement if neither yields a result.

The resulting ``UvAdhesionReport.placements`` feed into ``GeometryIR``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from frontend.models import ComponentExpansion, FrontendArtifact
from schema.geometry_ir import ComponentPlacement, PinPlacement
from schema.v6_ir import Point
from solver.units import MM_TO_UM

from .skeleton_router import SkeletonReport
from .uv_slot_search import SlotCandidate, search_slot


@dataclass
class UvAdhesionReport:
    placements: dict[str, ComponentPlacement] = field(default_factory=dict)
    """Final per-UV placements (anchor in mm, rotation deg, pad coords in mm)."""

    failed: list[tuple[str, str]] = field(default_factory=list)
    """``[(uv_name, reason)]`` for adhesions that fell back to seed position."""


def adhere_uv_components(
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    *,
    seed_anchor_mm: dict[str, tuple[float, float]],
    seed_rotation_deg: dict[str, float],
) -> UvAdhesionReport:
    report = UvAdhesionReport()
    # Build static obstacles once: board frame is enforced inside slot
    # search via the board arg; here we collect fixed-component bboxes.
    static_obstacles: list[tuple[float, float, float, float]] = []
    for comp in artifact.components.values():
        if comp.placement_kind == "fixed" and comp.bbox is not None:
            static_obstacles.append(
                (comp.bbox.min_x, comp.bbox.min_y, comp.bbox.max_x, comp.bbox.max_y)
            )

    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))

    # Routed host polylines (mm) keyed by net for fast lookup.
    edges_by_net: dict[str, list[str]] = {}
    for eid, edge in artifact.edges.items():
        if edge.net is None:
            continue
        route = skeleton.routes.get(eid)
        if route is None or not route.success or len(route.polyline_um) < 2:
            continue
        edges_by_net.setdefault(edge.net, []).append(eid)

    # Iterate UVs in deterministic order so already-placed bboxes are
    # consistent across runs.
    placed_obstacles: list[tuple[float, float, float, float]] = []
    for uv_name in sorted(artifact.uv_components.keys()):
        uv = artifact.uv_components[uv_name]
        placement = _place_uv(
            uv_name,
            uv,
            artifact,
            skeleton,
            edges_by_net,
            static_obstacles + placed_obstacles,
            (board_w, board_h),
            seed_anchor_mm,
            seed_rotation_deg,
        )
        if placement is not None:
            report.placements[uv_name] = placement
            # WI-F4: use rotated footprint bbox as obstacle (raw pad min/max
            # is degenerate for two-pad 0402 packages where both pads share y
            # → height ≈ 0 → subsequent UVs cannot see this footprint and
            # would overlap it visually).
            fw, fh = _footprint_size(uv)
            rot_q = int(round(float(placement.rotation_deg or 0.0))) % 180
            bw, bh = (fw, fh) if rot_q == 0 else (fh, fw)
            ax = float(placement.anchor.x)
            ay = float(placement.anchor.y)
            margin = 0.4
            placed_obstacles.append(
                (
                    ax - bw / 2.0 - margin,
                    ay - bh / 2.0 - margin,
                    ax + bw / 2.0 + margin,
                    ay + bh / 2.0 + margin,
                )
            )
        else:
            report.failed.append((uv_name, "could not derive placement"))
    return report


def _footprint_size(uv: ComponentExpansion) -> tuple[float, float]:
    xs = [float(p.local_x) for p in uv.pads]
    ys = [float(p.local_y) for p in uv.pads]
    if not xs:
        return (1.0, 1.0)
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    # Inflate by a pad clearance margin so RLC body is not cropped to
    # only pad-to-pad span.
    return (max(w, 1.0) + 0.6, max(h, 1.0) + 0.6)


def _place_uv(
    uv_name: str,
    uv: ComponentExpansion,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    obstacles: list[tuple[float, float, float, float]],
    board: tuple[float, float],
    seed_anchor_mm: dict[str, tuple[float, float]],
    seed_rotation_deg: dict[str, float],
) -> ComponentPlacement | None:
    if uv.uv_meta is None:
        return None

    # 1. Try slot-search (perpendicular adhesion on a host microstrip).
    cand = _try_slot_search(uv, artifact, skeleton, edges_by_net, obstacles, board)
    if cand is not None:
        return _materialise_from_slot(uv, cand)

    # 2. Fallback: legacy anchor-pin-on-routed-endpoint derivation.
    legacy = _legacy_anchor_placement(
        uv_name, uv, artifact, skeleton, edges_by_net, seed_anchor_mm, seed_rotation_deg
    )
    if legacy is None:
        return None
    # WI-F4: legacy path is obstacle-blind; nudge along host trace tangent
    # if rendered footprint bbox overlaps an existing obstacle.
    legacy = _nudge_clear_of_obstacles(
        legacy, uv, artifact, skeleton, edges_by_net, obstacles
    )
    return legacy


def _try_slot_search(
    uv: ComponentExpansion,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    obstacles: list[tuple[float, float, float, float]],
    board: tuple[float, float],
) -> SlotCandidate | None:
    meta = uv.uv_meta
    if meta is None or not meta.reference_net:
        return None
    host_eids = edges_by_net.get(meta.reference_net, [])
    if not host_eids:
        return None
    host_polylines: dict[str, list[tuple[float, float]]] = {}
    host_widths: dict[str, float] = {}
    for eid in host_eids:
        route = skeleton.routes.get(eid)
        if route is None:
            continue
        poly_mm = [(p[0] / MM_TO_UM, p[1] / MM_TO_UM) for p in route.polyline_um]
        if len(poly_mm) < 2:
            continue
        host_polylines[eid] = poly_mm
        edge = artifact.edges.get(eid)
        host_widths[eid] = (
            float(edge.width) if (edge and edge.width is not None) else 0.5
        )
    if not host_polylines:
        return None
    # Prefer non-seg1 edges; only fall back to seg1 when nothing else exists.
    non_seg1 = {eid: poly for eid, poly in host_polylines.items() if "_seg1" not in eid}
    search_poly = non_seg1 if non_seg1 else host_polylines
    search_widths = {eid: host_widths[eid] for eid in search_poly}
    fw, fh = _footprint_size(uv)
    return search_slot(
        footprint_size=(fw, fh),
        anchor_pin_local=_local_pin_offset(uv, meta.anchor_pin),
        host_polylines=search_poly,
        host_widths=search_widths,
        board=board,
        obstacles=obstacles,
        step_mm=0.8,  # smaller step → interior samples even on short seg2/seg3
    )


def _materialise_from_slot(
    uv: ComponentExpansion, cand: SlotCandidate
) -> ComponentPlacement:
    meta = uv.uv_meta
    assert meta is not None
    rotation = cand.rotation_deg
    cos_t = math.cos(math.radians(rotation))
    sin_t = math.sin(math.radians(rotation))
    anchor_pin = meta.anchor_pin
    lx, ly = _local_pin_offset(uv, anchor_pin)
    # Anchor pin must land on host_pin_xy.
    px = cand.host_pin_xy[0]
    py = cand.host_pin_xy[1]
    placement_x = px - (cos_t * lx - sin_t * ly)
    placement_y = py - (sin_t * lx + cos_t * ly)
    pad_placements = []
    for pad in uv.pads:
        plx, ply = float(pad.local_x), float(pad.local_y)
        ppx = placement_x + cos_t * plx - sin_t * ply
        ppy = placement_y + sin_t * plx + cos_t * ply
        pad_placements.append(PinPlacement(pin=pad.pin, point=Point(x=ppx, y=ppy)))
    return ComponentPlacement(
        component=uv.name,
        anchor=Point(x=cand.anchor_xy[0], y=cand.anchor_xy[1]),
        rotation_deg=_quantize_rotation(rotation),
        pads=tuple(pad_placements),
    )


def _legacy_anchor_placement(
    uv_name: str,
    uv: ComponentExpansion,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    seed_anchor_mm: dict[str, tuple[float, float]],
    seed_rotation_deg: dict[str, float],
) -> ComponentPlacement | None:
    if uv.uv_meta is None:
        return None
    anchor_pin = uv.uv_meta.anchor_pin
    anchor_endpoint = f"{uv_name}.{anchor_pin}"
    anchor_um = _lookup_endpoint_um(skeleton.final_endpoint_um, anchor_endpoint)
    other_um: tuple[int, int] | None = None
    other_pin: str | None = None
    for pad in uv.pads:
        if pad.pin == anchor_pin:
            continue
        ep = f"{uv_name}.{pad.pin}"
        ep_um = _lookup_endpoint_um(skeleton.final_endpoint_um, ep)
        if ep_um is not None:
            other_um = ep_um
            other_pin = pad.pin
            break

    if anchor_um is not None:
        anchor_xy = (anchor_um[0] / MM_TO_UM, anchor_um[1] / MM_TO_UM)
    else:
        anchor_xy = seed_anchor_mm.get(uv_name, (0.0, 0.0))

    rotation = _derive_rotation(uv, anchor_pin, anchor_um, other_pin, other_um)
    if rotation is None and uv.uv_meta.reference_net:
        # Derive perpendicular rotation from host edge direction (WI-F3:
        # falls back to geometric-nearest routed edge when name match fails).
        host_angle = _host_edge_tangent(
            anchor_endpoint,
            skeleton,
            edges_by_net,
            uv.uv_meta.reference_net,
            artifact,
            anchor_xy_mm=anchor_xy,
        )
        if host_angle is not None:
            rotation = host_angle + 90.0
    if rotation is None:
        rotation = float(seed_rotation_deg.get(uv_name, 0.0))
    rotation = _quantize_rotation(rotation)

    cos_t = math.cos(math.radians(rotation))
    sin_t = math.sin(math.radians(rotation))
    anchor_pad = next((p for p in uv.pads if p.pin == anchor_pin), None)
    if anchor_pad is None:
        return None
    local_x, local_y = _local_pin_offset(uv, anchor_pin)
    placement_x = anchor_xy[0] - (cos_t * local_x - sin_t * local_y)
    placement_y = anchor_xy[1] - (sin_t * local_x + cos_t * local_y)

    pad_placements = []
    for pad in uv.pads:
        lx, ly = _local_pin_offset(uv, pad.pin)
        ppx = placement_x + cos_t * lx - sin_t * ly
        ppy = placement_y + sin_t * lx + cos_t * ly
        pad_placements.append(PinPlacement(pin=pad.pin, point=Point(x=ppx, y=ppy)))

    return ComponentPlacement(
        component=uv_name,
        anchor=Point(x=placement_x, y=placement_y),
        rotation_deg=rotation,
        pads=tuple(pad_placements),
    )


def _local_pin_offset(uv: ComponentExpansion, pin: str) -> tuple[float, float]:
    for pad in uv.pads:
        if pad.pin == pin:
            return (float(pad.local_x), float(pad.local_y))
    return (0.0, 0.0)


def _rect_overlaps_any(
    rect: tuple[float, float, float, float],
    obstacles: list[tuple[float, float, float, float]],
    margin: float = 0.0,
) -> bool:
    x0, y0, x1, y1 = rect
    for ox0, oy0, ox1, oy1 in obstacles:
        if not (
            x1 + margin <= ox0
            or ox1 <= x0 - margin
            or y1 + margin <= oy0
            or oy1 <= y0 - margin
        ):
            return True
    return False


def _placement_bbox(
    placement: ComponentPlacement, uv: ComponentExpansion
) -> tuple[float, float, float, float]:
    fw, fh = _footprint_size(uv)
    rot_q = int(round(float(placement.rotation_deg or 0.0))) % 180
    bw, bh = (fw, fh) if rot_q == 0 else (fh, fw)
    ax = float(placement.anchor.x)
    ay = float(placement.anchor.y)
    return (ax - bw / 2.0, ay - bh / 2.0, ax + bw / 2.0, ay + bh / 2.0)


def _translate_placement(
    placement: ComponentPlacement, dx: float, dy: float
) -> ComponentPlacement:
    return ComponentPlacement(
        component=placement.component,
        anchor=Point(
            x=float(placement.anchor.x) + dx, y=float(placement.anchor.y) + dy
        ),
        rotation_deg=placement.rotation_deg,
        pads=tuple(
            PinPlacement(
                pin=p.pin, point=Point(x=float(p.point.x) + dx, y=float(p.point.y) + dy)
            )
            for p in placement.pads
        ),
    )


def _nudge_clear_of_obstacles(
    placement: ComponentPlacement,
    uv: ComponentExpansion,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    obstacles: list[tuple[float, float, float, float]],
) -> ComponentPlacement:
    """Shift legacy placement along host-trace tangent until bbox is clear.

    Generalised obstacle-aware adjustment for the legacy fallback path
    (slot search already handles obstacles). Walks symmetric offsets up
    to a few footprint heights; returns the first clear placement, else
    the original.
    """
    rect = _placement_bbox(placement, uv)
    if not _rect_overlaps_any(rect, obstacles, margin=0.05):
        return placement
    meta = uv.uv_meta
    if meta is None or not meta.reference_net:
        return placement
    anchor_endpoint = f"{uv.name}.{meta.anchor_pin}"
    tangent_deg = _host_edge_tangent(
        anchor_endpoint,
        skeleton,
        edges_by_net,
        meta.reference_net,
        artifact,
        anchor_xy_mm=(float(placement.anchor.x), float(placement.anchor.y)),
    )
    if tangent_deg is None:
        return placement
    tx = math.cos(math.radians(tangent_deg))
    ty = math.sin(math.radians(tangent_deg))
    fw, fh = _footprint_size(uv)
    step = max(fw, fh) + 0.4
    for k in range(1, 6):
        for sign in (1.0, -1.0):
            dx = sign * k * step * tx
            dy = sign * k * step * ty
            candidate = _translate_placement(placement, dx, dy)
            cand_rect = _placement_bbox(candidate, uv)
            if not _rect_overlaps_any(cand_rect, obstacles, margin=0.05):
                return candidate
    return placement


def _host_edge_tangent(
    anchor_endpoint: str,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    net: str,
    artifact: FrontendArtifact,
    *,
    anchor_xy_mm: tuple[float, float] | None = None,
) -> float | None:
    """Return the trace angle (degrees) of the host edge near anchor_endpoint.

    WI-F3: tries two strategies, in order:

    1. **Name match** — find a routed host edge whose ``connections`` list
       contains ``anchor_endpoint``.  Skips degenerate (<2 pt) routes.
    2. **Geometric nearest** — if (1) fails (e.g. the matching edge is a
       1-point stub or anchor_endpoint is a synthetic id absent from any
       connections list), pick the routed edge whose polyline lies closest
       to ``anchor_xy_mm`` and return its overall tangent.

    Returns None only when no usable routed edge exists on the net.
    """

    def _route_tangent(poly_um: list[tuple[int, int]]) -> float | None:
        if len(poly_um) < 2:
            return None
        dx = poly_um[-1][0] - poly_um[0][0]
        dy = poly_um[-1][1] - poly_um[0][1]
        if dx == 0 and dy == 0:
            return None
        return math.degrees(math.atan2(dy, dx))

    # Strategy 1: name match on edge.connections.
    for eid in edges_by_net.get(net, []):
        edge = artifact.edges.get(eid)
        if edge is None or anchor_endpoint not in edge.connections:
            continue
        route = skeleton.routes.get(eid)
        if route is None or not route.success:
            continue
        angle = _route_tangent(list(route.polyline_um))
        if angle is not None:
            return angle

    # Strategy 2: geometric nearest routed edge in the net.
    if anchor_xy_mm is None:
        return None
    ax, ay = anchor_xy_mm
    best_d = math.inf
    best_angle: float | None = None
    for eid in edges_by_net.get(net, []):
        route = skeleton.routes.get(eid)
        if route is None or not route.success or len(route.polyline_um) < 2:
            continue
        poly_mm = [(p[0] / MM_TO_UM, p[1] / MM_TO_UM) for p in route.polyline_um]
        for j in range(len(poly_mm) - 1):
            d = _point_seg_distance_mm((ax, ay), poly_mm[j], poly_mm[j + 1])
            if d < best_d:
                best_d = d
                best_angle = _route_tangent(list(route.polyline_um))
    return best_angle


def _point_seg_distance_mm(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """Shortest distance from point p to segment ab (mm)."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def _derive_rotation(
    uv: ComponentExpansion,
    anchor_pin: str,
    anchor_um: tuple[int, int] | None,
    other_pin: str | None,
    other_um: tuple[int, int] | None,
) -> float | None:
    if anchor_um is None or other_um is None:
        return None
    dx = other_um[0] - anchor_um[0]
    dy = other_um[1] - anchor_um[1]
    if dx == 0 and dy == 0:
        return None
    return math.degrees(math.atan2(dy, dx))


def _quantize_rotation(rotation: float) -> float:
    allowed = (0.0, 90.0, 180.0, -90.0, 270.0)
    angle = ((rotation + 180.0) % 360.0) - 180.0
    best = min(allowed, key=lambda a: abs(_diff(angle, a)))
    return float(best)


def _diff(a: float, b: float) -> float:
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


def _lookup_endpoint_um(
    endpoint_map: dict[str, tuple[int, int]], endpoint: str
) -> tuple[int, int] | None:
    exact = endpoint_map.get(endpoint)
    if exact is not None:
        return exact
    for key, value in endpoint_map.items():
        if "," not in key:
            continue
        parts = [part.strip() for part in key.split(",") if part.strip()]
        if endpoint in parts:
            return value
    return None


__all__ = ["UvAdhesionReport", "adhere_uv_components"]
