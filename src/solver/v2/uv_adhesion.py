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
from .uv_classify import UvKind, classify_uv
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
    # WI-G3: place series UVs first so shunt/slot-search placements can
    # treat their pad span as obstacles. Within each group, alphabetical.
    def _order_key(name: str) -> tuple[int, str]:
        kind = classify_uv(artifact.uv_components[name])
        # series first (0), then shunt/probe (1)
        return (0 if kind == UvKind.SERIES else 1, name)

    placed_obstacles: list[tuple[float, float, float, float]] = []
    used_series_endpoints: set[tuple[float, float]] = set()
    for uv_name in sorted(artifact.uv_components.keys(), key=_order_key):
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
            used_series_endpoints,
        )
        if placement is not None:
            report.placements[uv_name] = placement
            # WI-F4/G3: use rotated footprint bbox sized to actual pad
            # extent (series components may stretch beyond local footprint).
            fw, fh = _footprint_size(uv)
            rot_q = int(round(float(placement.rotation_deg or 0.0))) % 180
            bw, bh = (fw, fh) if rot_q == 0 else (fh, fw)
            ax = float(placement.anchor.x)
            ay = float(placement.anchor.y)
            # Expand to enclose actual pad positions
            pad_xs = [float(p.point.x) for p in placement.pads]
            pad_ys = [float(p.point.y) for p in placement.pads]
            if pad_xs and pad_ys:
                pad_perp = max(_pad_perp_size(uv, p.pin) for p in placement.pads)
                pad_min_x = min(pad_xs) - pad_perp * 0.5
                pad_max_x = max(pad_xs) + pad_perp * 0.5
                pad_min_y = min(pad_ys) - pad_perp * 0.5
                pad_max_y = max(pad_ys) + pad_perp * 0.5
                bw = max(bw, pad_max_x - pad_min_x)
                bh = max(bh, pad_max_y - pad_min_y)
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


def _pad_perp_size(uv: ComponentExpansion, pin: str) -> float:
    """WI-G2: pad dimension perpendicular to body long-axis (mm).

    For default-rotated CAP/RES the body's long axis runs along local-x
    (pads at ±local_x, both at local_y=0), so the perp dimension is the
    pad's smaller side. Falls back to footprint short side when
    ``pad_geometry`` is absent.
    """
    for pad in uv.pads:
        if pad.pin == pin:
            w = float(pad.pad_width) if pad.pad_width else 0.0
            ln = float(pad.pad_length) if pad.pad_length else 0.0
            if w > 0 and ln > 0:
                return min(w, ln)
    fw, fh = _footprint_size(uv)
    return min(fw, fh)


def _edge_align_shift(
    uv: ComponentExpansion,
    anchor_pin: str,
    rotation_deg: float,
    trace_width: float,
) -> tuple[float, float]:
    """WI-H1: compute (dx, dy) to shift placement so the anchor pad embeds
    about half its width into the trace. Inner edge sits near the trace
    centerline, outer edge extends beyond the trace edge, ensuring the
    non-anchor pad stays clear and doesn't short to the trace.

    Geometry: the body extends from the anchor pin away from the trace
    (anchor pin local offset != (0,0) for two-pin RLCs). The unit vector
    from anchor pin local toward body center (= origin (0,0)) gives the
    "into body" direction in local frame; rotated to world coordinates
    it becomes the shift direction. Magnitude = pad_perp/2.
    """
    alx, aly = _local_pin_offset(uv, anchor_pin)
    bx, by = -alx, -aly  # anchor pin → body center, local frame
    norm = math.hypot(bx, by)
    if norm < 1e-9:
        return (0.0, 0.0)
    bx /= norm
    by /= norm
    cos_t = math.cos(math.radians(rotation_deg))
    sin_t = math.sin(math.radians(rotation_deg))
    wx = cos_t * bx - sin_t * by  # rotate to world
    wy = sin_t * bx + cos_t * by
    pad_perp = _pad_perp_size(uv, anchor_pin)
    mag = pad_perp * 0.5
    return (wx * mag, wy * mag)


def _infer_anchor_from_virtual_stub(
    uv_name: str,
    anchor_pin: str,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    reference_net: str | None,
) -> tuple[int, int] | None:
    """WI-H2: infer anchor endpoint from a virtual stub edge (zero-length
    placeholder created for topology but not actually routed). Virtual stubs
    like IC1_pin1_seg2_to_C7 have `polyline_um` with 1 point and were filtered
    by WI-G4 from slot-search, but we can extract the stub's far-end real pin
    endpoint from final_endpoint_um.

    Returns anchor coordinate in μm if found, else None.
    """
    if not reference_net:
        return None
    # Search for edge named *_to_{uv_name} on the reference net.
    for eid in edges_by_net.get(reference_net, []):
        if not eid.endswith(f"_to_{uv_name}"):
            continue
        route = skeleton.routes.get(eid)
        if route is None or not route.success:
            continue
        # Stub edges typically have 1-point polylines (degenerate).
        # The connections field has "A → B" where B is the UV's anchor pin.
        # We want to extract A's coordinate from final_endpoint_um.
        conns = getattr(route, "connections", None)
        if not conns:
            continue
        # Connection format: "IC1.PIN_1 → C7.PIN_1"
        parts = conns.split(" → ")
        if len(parts) != 2:
            continue
        far_ep = parts[0].strip()
        near_ep = parts[1].strip()
        # Validate that near_ep matches our UV anchor pin.
        expected_near = f"{uv_name}.{anchor_pin}"
        if near_ep != expected_near:
            continue
        # Lookup far_ep in final_endpoint_um.
        far_um = _lookup_endpoint_um(skeleton.final_endpoint_um, far_ep)
        if far_um is not None:
            return far_um
    return None


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
    used_series_endpoints: set[tuple[float, float]],
) -> ComponentPlacement | None:
    if uv.uv_meta is None:
        return None

    # WI-G3: series components — both pins land on respective net hosts.
    if classify_uv(uv) == UvKind.SERIES:
        series = _place_series_uv(
            uv_name, uv, skeleton, edges_by_net, used_series_endpoints
        )
        if series is not None:
            return series

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
    # WI-G4: skip virtual stub edges (e.g. "IC1_pin1_seg2_to_C7") that
    # carry no rendered routing and would mislead the slot search.
    host_eids = [eid for eid in host_eids if "_to_" not in eid]
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
    # WI-G2: shift anchor pin off the trace centerline so pad's outer
    # edge sits on the trace edge.
    sdx, sdy = _edge_align_shift(uv, anchor_pin, rotation, cand.trace_width)
    px = cand.host_pin_xy[0] + sdx
    py = cand.host_pin_xy[1] + sdy
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
        anchor=Point(x=placement_x, y=placement_y),
        rotation_deg=_quantize_rotation(rotation),
        pads=tuple(pad_placements),
    )


def _place_series_uv(
    uv_name: str,
    uv: ComponentExpansion,
    skeleton: SkeletonReport,
    edges_by_net: dict[str, list[str]],
    used_endpoints: set[tuple[float, float]],
) -> ComponentPlacement | None:
    """WI-G3: place a series RLC with both pins on routed endpoints of
    their respective nets.  Greedy: pick the closest pair of routed-edge
    endpoints (one per pin net). Body axis runs from PIN_1 → PIN_2.
    """
    pn = dict(uv.pin_nets or {})
    if len(pn) < 2:
        return None
    pins = list(pn.keys())[:2]
    nets = [pn[pins[0]], pn[pins[1]]]
    eps_per_pin: list[list[tuple[float, float]]] = []
    for net in nets:
        eps: list[tuple[float, float]] = []
        for eid in edges_by_net.get(net, []):
            if "_to_" in eid:
                continue
            route = skeleton.routes.get(eid)
            if route is None or not route.success or len(route.polyline_um) < 2:
                continue
            poly = [(p[0] / MM_TO_UM, p[1] / MM_TO_UM) for p in route.polyline_um]
            eps.append(poly[0])
            eps.append(poly[-1])
        eps_per_pin.append(eps)
    if not eps_per_pin[0] or not eps_per_pin[1]:
        return None
    best: tuple[float, tuple[float, float], tuple[float, float]] | None = None
    for p1 in eps_per_pin[0]:
        for p2 in eps_per_pin[1]:
            k1 = (round(p1[0], 4), round(p1[1], 4))
            k2 = (round(p2[0], 4), round(p2[1], 4))
            if k1 in used_endpoints or k2 in used_endpoints:
                continue
            d = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
            if best is None or d < best[0]:
                best = (d, p1, p2)
    if best is None:
        return None
    _, p1, p2 = best
    # Limit stretch: if endpoints are too far, fallback to None so the
    # slot-search / legacy paths handle this UV instead.
    fw, _fh = _footprint_size(uv)
    if best[0] > max(5.0 * fw, 20.0):
        return None
    used_endpoints.add((round(p1[0], 4), round(p1[1], 4)))
    used_endpoints.add((round(p2[0], 4), round(p2[1], 4)))
    angle = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
    rotation = _quantize_rotation(angle)
    anchor_x = (p1[0] + p2[0]) * 0.5
    anchor_y = (p1[1] + p2[1]) * 0.5
    pads = (
        PinPlacement(pin=pins[0], point=Point(x=float(p1[0]), y=float(p1[1]))),
        PinPlacement(pin=pins[1], point=Point(x=float(p2[0]), y=float(p2[1]))),
    )
    return ComponentPlacement(
        component=uv_name,
        anchor=Point(x=anchor_x, y=anchor_y),
        rotation_deg=rotation,
        pads=pads,
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
    # WI-H2: if anchor endpoint not found, try inferring from virtual stub.
    if anchor_um is None:
        anchor_um = _infer_anchor_from_virtual_stub(
            uv_name, anchor_pin, skeleton, edges_by_net, uv.uv_meta.reference_net
        )
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
    # WI-G2: shift anchor pin off trace centerline so pad outer edge sits
    # on trace edge. Look up the host edge width on reference_net.
    trace_w = 0.5
    if uv.uv_meta.reference_net:
        for eid in edges_by_net.get(uv.uv_meta.reference_net, []):
            if "_to_" in eid:
                continue
            edge = artifact.edges.get(eid)
            if edge and edge.width is not None:
                trace_w = float(edge.width)
                break
    sdx, sdy = _edge_align_shift(uv, anchor_pin, rotation, trace_w)
    anchor_world_x = anchor_xy[0] + sdx
    anchor_world_y = anchor_xy[1] + sdy
    placement_x = anchor_world_x - (cos_t * local_x - sin_t * local_y)
    placement_y = anchor_world_y - (sin_t * local_x + cos_t * local_y)

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
