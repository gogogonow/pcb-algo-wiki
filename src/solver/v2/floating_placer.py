"""WI-J3 — SA placer for floating components (PhaseC step 1).

对所有 ``placement_kind == "floating"`` 的组件（即 yaml 中 ``is_floating: true``
但 ``x/y`` 缺失）用模拟退火搜索合法 ``(x, y, rotation)``：

* **越界惩罚**：组件 bbox 任意方向超出板框 → 平方距离惩罚
* **重叠惩罚**：与下列静态障碍重叠 → 重叠面积平方惩罚
    - 已 fixed 组件 bbox（含 clearance 膨胀）
    - 已 UV-adhered 组件 bbox（从 adhesion.placements 的 pads 推算）
    - 已 successful 微带线 polyline 的逐段 bbox（含 0.5mm padding + clearance）
* **HPWL**：每条 edge 中涉及 floating 的端点 → 半周长线长，引导紧凑布局

输出 ``FloatingPlacement(x, y, rotation)``。orchestrator 负责把它转成
``ComponentPlacement`` 写入 GeometryIR。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable

from frontend.models import ComponentExpansion, FrontendArtifact
from schema.geometry_ir import ComponentPlacement
from solver.units import um_to_mm

from .skeleton_router import SkeletonReport
from .uv_adhesion import UvAdhesionReport


@dataclass(frozen=True)
class FloatingPlacement:
    x: float
    y: float
    rotation: int  # ∈ {0, 90, 180, -90}


@dataclass(frozen=True)
class FloatingPlacerConfig:
    iterations: int = 8000
    temperature_init: float = 5000.0
    temperature_min: float = 1.0
    cooling: float = 0.9985
    step_mm: float = 1.2
    boundary_weight: float = 400.0
    overlap_weight: float = 2000.0
    body_overlap_weight: float = 500.0
    hpwl_weight: float = 20.0
    routability_weight: float = 50.0
    escape_alignment_weight: float = 1.0
    rf_bus_cross_weight: float = 150.0
    airwire_cross_weight: float = 20.0
    clearance_mm: float = 0.3
    margin_mm: float = 0.5  # 与板框、其他障碍的最小距离裕量
    seed: int = 0xC0FFEE
    # M4: SA early-stop — terminate (returning best_state) when best energy
    # hasn't improved for ``no_improve_patience`` consecutive iterations.
    # Disabled when set to 0 (legacy behaviour: run all iterations).
    no_improve_patience: int = 0
    # Relative improvement threshold below which a step is "not an improvement".
    no_improve_min_rel: float = 1e-4


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def place_floating_components(
    *,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    board_w: float,
    board_h: float,
    config: FloatingPlacerConfig | None = None,
) -> dict[str, FloatingPlacement]:
    cfg = config or FloatingPlacerConfig()
    rng = random.Random(cfg.seed)

    floating_names = [
        n for n, c in artifact.components.items() if c.placement_kind == "floating"
    ]
    if not floating_names:
        return {}

    obstacles = _collect_static_obstacles(
        artifact=artifact,
        skeleton=skeleton,
        adhesion=adhesion,
        clearance=cfg.clearance_mm,
    )

    # Build "connected pairs" — components linked by a flex edge are allowed
    # to share body bbox space (their pads must touch).  We only penalise body
    # overlap for *unrelated* component pairs.
    connected_pairs: set[frozenset[str]] = set()
    for edge in artifact.edges.values():
        comps = []
        for ep in edge.connections or ():
            if "." in ep:
                comps.append(ep.split(".", 1)[0])
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                if comps[i] != comps[j]:
                    connected_pairs.add(frozenset({comps[i], comps[j]}))

    # Initialise each floating component at the centroid of its connected
    # non-floating endpoints (with a small deterministic jitter to break
    # symmetry between components sharing the same anchor).  This biases SA
    # toward layouts where the floating component is "near" its neighbours so
    # routability has a real chance of converging.
    state: dict[str, FloatingPlacement] = {}
    floating_name_set = set(floating_names)
    for i, name in enumerate(floating_names):
        comp = artifact.components[name]
        w, h = _comp_wh(comp, rotation=0)
        anchors: list[tuple[float, float]] = []
        for edge in artifact.edges.values():
            conns = edge.connections or ()
            if not any(ep.split(".", 1)[0] == name for ep in conns if "." in ep):
                continue
            for ep in conns:
                comp_id = ep.split(".", 1)[0] if "." in ep else None
                if comp_id == name or comp_id in floating_name_set:
                    continue
                xy = _resolve_endpoint(artifact, skeleton, adhesion, ep)
                if xy is not None:
                    anchors.append(xy)
        if anchors:
            # Escape-aware centroid: shift each fixed pin in the direction its
            # natural pin-escape would go, by ESCAPE_OFFSET_MM, so the floating
            # component lands on the OPEN side of each fixed pin rather than
            # behind its host component body.
            ESCAPE_OFFSET_MM = 3.0
            shifted: list[tuple[float, float]] = []
            for edge in artifact.edges.values():
                conns = edge.connections or ()
                if not any(ep.split(".", 1)[0] == name for ep in conns if "." in ep):
                    continue
                for ep in conns:
                    comp_id = ep.split(".", 1)[0] if "." in ep else None
                    if comp_id == name or comp_id in floating_name_set:
                        continue
                    xy = _resolve_endpoint(artifact, skeleton, adhesion, ep)
                    if xy is None:
                        continue
                    dx, dy = _pin_escape_global_dir(artifact, ep)
                    shifted.append(
                        (xy[0] + dx * ESCAPE_OFFSET_MM, xy[1] + dy * ESCAPE_OFFSET_MM)
                    )
            if shifted:
                cx = sum(p[0] for p in shifted) / len(shifted)
                cy = sum(p[1] for p in shifted) / len(shifted)
            else:
                cx = sum(p[0] for p in anchors) / len(anchors)
                cy = sum(p[1] for p in anchors) / len(anchors)
            jitter_x = (i % 3 - 1) * 1.5
            jitter_y = ((i // 3) % 3 - 1) * 1.5
            x0 = cx + jitter_x
            y0 = cy + jitter_y
        else:
            # Fallback: spread along right-top grid (original heuristic).
            col = i % 2
            row = i // 2
            x0 = board_w * 0.65 + col * 6.0
            y0 = board_h * 0.55 + row * 6.0
        # Clamp inside the board.
        x0 = min(max(x0, w / 2 + cfg.margin_mm), board_w - w / 2 - cfg.margin_mm)
        y0 = min(max(y0, h / 2 + cfg.margin_mm), board_h - h / 2 - cfg.margin_mm)
        state[name] = FloatingPlacement(x=x0, y=y0, rotation=0)

    def energy(s: dict[str, FloatingPlacement]) -> float:
        e = 0.0
        bboxes = [_bbox_for(artifact, n, p) for n, p in s.items()]
        body_bboxes = [(n, _body_bbox_for(artifact, n, p)) for n, p in s.items()]
        for bb in bboxes:
            e += cfg.boundary_weight * _out_of_board_sq(
                bb, board_w, board_h, cfg.margin_mm
            )
            for ob in obstacles:
                e += cfg.overlap_weight * _bbox_overlap_linear(bb, ob)
        for i in range(len(bboxes)):
            for j in range(i + 1, len(bboxes)):
                e += cfg.overlap_weight * _bbox_overlap_linear(bboxes[i], bboxes[j])
        # Body-vs-body overlap (uses full footprint envelope so large IC
        # bodies prevent small caps being placed *on top of* them).  Skip
        # connected pairs — their pads must touch.
        for i in range(len(body_bboxes)):
            for j in range(i + 1, len(body_bboxes)):
                ni, nj = body_bboxes[i][0], body_bboxes[j][0]
                if frozenset({ni, nj}) in connected_pairs:
                    continue
                e += cfg.body_overlap_weight * _bbox_overlap_area_sq(
                    body_bboxes[i][1], body_bboxes[j][1]
                )
        # Floating body vs fixed-component body bbox (use comp.bbox if known)
        for name, bb_body in body_bboxes:
            for fixed_name, fixed in artifact.components.items():
                if fixed.placement_kind != "fixed" or fixed.bbox is None:
                    continue
                if frozenset({name, fixed_name}) in connected_pairs:
                    continue
                fixed_bb = (
                    fixed.bbox.min_x,
                    fixed.bbox.min_y,
                    fixed.bbox.max_x,
                    fixed.bbox.max_y,
                )
                e += cfg.body_overlap_weight * _bbox_overlap_area_sq(bb_body, fixed_bb)
        e += cfg.hpwl_weight * _hpwl_energy(artifact, s, skeleton, adhesion)
        e += cfg.routability_weight * _routability_cost(
            artifact, s, skeleton, adhesion, obstacles
        )
        e += cfg.rf_bus_cross_weight * _rf_bus_cross_cost(
            artifact, s, skeleton, adhesion
        )
        e += cfg.airwire_cross_weight * _airwire_cross_cost(
            artifact, s, skeleton, adhesion
        )
        return e

    cur_e = energy(state)
    best_state, best_e = dict(state), cur_e
    T = cfg.temperature_init
    no_improve = 0
    patience = max(0, int(cfg.no_improve_patience))
    rel_threshold = float(cfg.no_improve_min_rel)

    for _ in range(cfg.iterations):
        if T < cfg.temperature_min:
            break
        name = rng.choice(floating_names)
        old = state[name]
        if rng.random() < 0.12:
            new_rot = rng.choice([r for r in (0, 90, 180, -90) if r != old.rotation])
            new_p = FloatingPlacement(old.x, old.y, new_rot)
        else:
            dx = rng.uniform(-cfg.step_mm, cfg.step_mm)
            dy = rng.uniform(-cfg.step_mm, cfg.step_mm)
            new_p = FloatingPlacement(old.x + dx, old.y + dy, old.rotation)
        state[name] = new_p
        new_e = energy(state)
        if new_e < cur_e or rng.random() < math.exp(-(new_e - cur_e) / max(T, 1e-6)):
            cur_e = new_e
            if cur_e < best_e:
                improvement = (best_e - cur_e) / max(abs(best_e), 1e-9)
                best_state, best_e = dict(state), cur_e
                if improvement >= rel_threshold:
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                no_improve += 1
        else:
            state[name] = old
            no_improve += 1
        T *= cfg.cooling
        if patience and no_improve >= patience:
            break

    return best_state


# ----------------------------------------------------------------------
# Build ComponentPlacement from FloatingPlacement
# ----------------------------------------------------------------------


def build_component_placement(
    name: str,
    comp: ComponentExpansion,
    place: FloatingPlacement,
) -> ComponentPlacement:
    """实例化 floating 组件为 GeometryIR 的 ComponentPlacement。"""
    from schema.geometry_ir import PinPlacement
    from schema.v6_ir import Point

    cos_t, sin_t = _rot(float(place.rotation))
    pads: list[PinPlacement] = []
    for pad in comp.pads:
        lx = float(pad.local_x or 0.0)
        ly = float(pad.local_y or 0.0)
        abs_x = place.x + cos_t * lx - sin_t * ly
        abs_y = place.y + sin_t * lx + cos_t * ly
        pads.append(PinPlacement(pin=pad.pin, point=Point(x=abs_x, y=abs_y)))
    return ComponentPlacement(
        component=name,
        anchor=Point(x=place.x, y=place.y),
        rotation_deg=float(place.rotation),
        pads=tuple(pads),
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _rot(deg: float) -> tuple[float, float]:
    th = math.radians(deg)
    return math.cos(th), math.sin(th)


def _comp_wh(comp: ComponentExpansion, rotation: int) -> tuple[float, float]:
    """组件 axis-aligned bbox 的 (width, height) — 假设 footprint length 沿 x、width 沿 y。

    若 footprint dimensions 不可用，用 pads 包络的兜底值。
    """
    # 沿用 expand_components 的几何：length=x方向尺寸，width=y方向尺寸
    # 但 ComponentExpansion 并不直接持有 footprint dims；改用 pads 包络。
    if comp.pads:
        # 用 local 坐标包络估算
        xs = [float(p.local_x or 0.0) for p in comp.pads]
        ys = [float(p.local_y or 0.0) for p in comp.pads]
        # 加焊盘自身尺寸的一半，保证 bbox 包住焊盘
        half_pw = max((float(p.pad_width or 0.0) for p in comp.pads), default=0.0) / 2
        half_pl = max((float(p.pad_length or 0.0) for p in comp.pads), default=0.0) / 2
        w_local = (max(xs) - min(xs)) + 2 * half_pl  # x 方向
        h_local = (max(ys) - min(ys)) + 2 * half_pw  # y 方向
    else:
        w_local, h_local = 2.0, 2.0
    if rotation in (90, -90):
        return h_local, w_local
    return w_local, h_local


def _comp_body_wh(comp: ComponentExpansion, rotation: int) -> tuple[float, float]:
    """Full body (footprint) envelope when known, else fall back to pad envelope."""
    if comp.footprint_dims_mm is not None:
        w_body, l_body = comp.footprint_dims_mm
        w_local, h_local = l_body, w_body
        if rotation in (90, -90):
            return h_local, w_local
        return w_local, h_local
    return _comp_wh(comp, rotation)


def _body_bbox_for(
    artifact: FrontendArtifact,
    name: str,
    place: FloatingPlacement,
) -> tuple[float, float, float, float]:
    comp = artifact.components[name]
    w, h = _comp_body_wh(comp, place.rotation)
    return (place.x - w / 2, place.y - h / 2, place.x + w / 2, place.y + h / 2)


def _bbox_for(
    artifact: FrontendArtifact,
    name: str,
    place: FloatingPlacement,
) -> tuple[float, float, float, float]:
    comp = artifact.components[name]
    w, h = _comp_wh(comp, place.rotation)
    return (place.x - w / 2, place.y - h / 2, place.x + w / 2, place.y + h / 2)


def _out_of_board_sq(
    bb: tuple[float, float, float, float],
    W: float,
    H: float,
    margin: float,
) -> float:
    dx = max(0.0, margin - bb[0]) + max(0.0, bb[2] - (W - margin))
    dy = max(0.0, margin - bb[1]) + max(0.0, bb[3] - (H - margin))
    return dx * dx + dy * dy


def _bbox_overlap_area_sq(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ow = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oh = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = ow * oh
    return area * area


def _bbox_overlap_linear(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Linear-in-area overlap with a fixed offset for any positive overlap.

    Used for pad-vs-pad overlap so that *tiny* overlaps (e.g. 0.02mm strip)
    still incur a meaningful penalty (squared-area collapses to ~0 there).
    """
    ow = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oh = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = ow * oh
    if area <= 0.0:
        return 0.0
    # Linear-in-area term — gives clear gradient and dominates the
    # squared-area term for very small overlaps.
    return area


def _collect_static_obstacles(
    *,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    clearance: float,
) -> list[tuple[float, float, float, float]]:
    obs: list[tuple[float, float, float, float]] = []
    # fixed 组件 bbox（来自 ComponentExpansion）
    for comp in artifact.components.values():
        if comp.placement_kind != "fixed" or comp.bbox is None:
            continue
        obs.append(
            (
                comp.bbox.min_x - clearance,
                comp.bbox.min_y - clearance,
                comp.bbox.max_x + clearance,
                comp.bbox.max_y + clearance,
            )
        )
    # 已 UV-adhered 组件：用 pad 中心 + pad 尺寸推算 bbox
    for placement in adhesion.placements.values():
        bb = _placement_pad_bbox(placement)
        if bb is None:
            continue
        obs.append(
            (
                bb[0] - clearance,
                bb[1] - clearance,
                bb[2] + clearance,
                bb[3] + clearance,
            )
        )
    # 已成功的微带线 polyline：逐段 bbox 扩展
    for route in skeleton.routes.values():
        if not route.success or len(route.polyline_um) < 2:
            continue
        # Use the real route half-width from the artifact edge definition so
        # that wide RF routes (e.g. 3.6 mm) create a correctly-sized obstacle.
        artifact_edge = artifact.edges.get(route.edge_id)
        half_w = float(artifact_edge.width or 0.5) / 2.0 if artifact_edge else 0.5
        pad = half_w + clearance
        pts_mm = [(um_to_mm(x), um_to_mm(y)) for x, y in route.polyline_um]
        for (x1, y1), (x2, y2) in zip(pts_mm, pts_mm[1:]):
            obs.append(
                (
                    min(x1, x2) - pad,
                    min(y1, y2) - pad,
                    max(x1, x2) + pad,
                    max(y1, y2) + pad,
                )
            )
    return obs


def _placement_pad_bbox(
    placement: ComponentPlacement,
) -> tuple[float, float, float, float] | None:
    if not placement.pads:
        return None
    xs = [float(p.point.x) for p in placement.pads]
    ys = [float(p.point.y) for p in placement.pads]
    # Tight pad envelope — the per-obstacle clearance is added by the caller
    # in ``_collect_static_obstacles`` so we don't double-pad here.
    pad = 0.3
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _floating_pin_xy(
    comp: ComponentExpansion, pin_name: str, fp: FloatingPlacement
) -> tuple[float, float] | None:
    """Resolve absolute (x, y) for a pin on a floating component given the
    current SA candidate placement.  Used by routability cost so direct-line
    sampling actually originates at the pin (not at the component anchor)."""
    cos_t, sin_t = _rot(float(fp.rotation))
    for pad in comp.pads:
        if pad.pin == pin_name:
            lx = float(pad.local_x or 0.0)
            ly = float(pad.local_y or 0.0)
            return (fp.x + cos_t * lx - sin_t * ly, fp.y + sin_t * lx + cos_t * ly)
    return None


def _routability_cost(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    obstacles: list[tuple[float, float, float, float]],
) -> float:
    """For every edge with a floating endpoint, sample points along the direct
    line between its endpoints (0.5 mm step) and count how many fall inside
    any static obstacle bbox.  This biases SA toward positions where the
    direct connection is unobstructed.

    Each blocked sample contributes a unit of cost; SA multiplies by
    ``cfg.routability_weight`` to scale against overlap / boundary.
    """
    if not obstacles:
        return 0.0
    floating_names = set(state.keys())
    cost = 0.0
    sample_step = 0.5  # mm
    # M5: bucket obstacles into a coarse uniform grid for sub-linear sample
    # lookup.  Equivalent semantics — every obstacle that contains a sample
    # was reachable in the legacy linear scan and is still found here.
    bucket_mm = 4.0
    inv_b = 1.0 / bucket_mm
    bucket_map: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for ob in obstacles:
        ix0 = int(math.floor(ob[0] * inv_b))
        iy0 = int(math.floor(ob[1] * inv_b))
        ix1 = int(math.floor(ob[2] * inv_b))
        iy1 = int(math.floor(ob[3] * inv_b))
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                bucket_map.setdefault((ix, iy), []).append(ob)
    for edge in artifact.edges.values():
        conns = edge.connections or ()
        if not any(ep.split(".", 1)[0] in floating_names for ep in conns if "." in ep):
            continue
        # Resolve endpoint coordinates.  Use floating component ANCHOR (not
        # pin centre) so a rotation move doesn't cause routability cost to
        # jitter wildly — this keeps SA acceptance stable while still giving
        # a useful signal about whether the component sits in a clear lane.
        points: list[tuple[float, float]] = []
        for ep in conns:
            comp_id = ep.split(".", 1)[0] if "." in ep else None
            if comp_id and comp_id in floating_names:
                fp = state[comp_id]
                points.append((fp.x, fp.y))
                continue
            xy = _resolve_endpoint(artifact, skeleton, adhesion, ep)
            if xy is not None:
                points.append(xy)
        if len(points) < 2:
            continue
        # Sample direct segments between consecutive endpoints.
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 1e-9:
                continue
            steps = max(2, int(length / sample_step))
            for k in range(1, steps):  # skip the endpoints themselves
                t = k / steps
                sx = x1 + (x2 - x1) * t
                sy = y1 + (y2 - y1) * t
                key = (int(math.floor(sx * inv_b)), int(math.floor(sy * inv_b)))
                bucket = bucket_map.get(key)
                if not bucket:
                    continue
                for ob in bucket:
                    if ob[0] <= sx <= ob[2] and ob[1] <= sy <= ob[3]:
                        cost += 1.0
                        break
    return cost


def _hpwl_energy(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
) -> float:
    e = 0.0
    floating_names = set(state.keys())
    for edge in artifact.edges.values():
        conns: Iterable[str] = edge.connections or ()
        endpoints: list[tuple[float, float]] = []
        any_float = False
        for ep in conns:
            comp_id = ep.split(".", 1)[0] if "." in ep else None
            if comp_id and comp_id in floating_names:
                any_float = True
                # 用 floating 当前 anchor 近似（pin 在 component 上下偏移很小）
                fp = state[comp_id]
                endpoints.append((fp.x, fp.y))
                continue
            xy = _resolve_endpoint(artifact, skeleton, adhesion, ep)
            if xy is not None:
                endpoints.append(xy)
        if any_float and endpoints:
            xs = [p[0] for p in endpoints]
            ys = [p[1] for p in endpoints]
            e += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return e


def _resolve_endpoint(
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    ep: str,
) -> tuple[float, float] | None:
    if "." in ep:
        comp_id, pin_id = ep.split(".", 1)
        comp = artifact.components.get(comp_id)
        if comp is not None:
            for pad in comp.pads:
                if (
                    pad.pin == pin_id
                    and pad.abs_x is not None
                    and pad.abs_y is not None
                ):
                    return (float(pad.abs_x), float(pad.abs_y))
        place = adhesion.placements.get(comp_id)
        if place is not None:
            for pp in place.pads:
                if pp.pin == pin_id:
                    return (float(pp.point.x), float(pp.point.y))
    xy_um = skeleton.final_endpoint_um.get(ep)
    if xy_um is not None:
        return (um_to_mm(xy_um[0]), um_to_mm(xy_um[1]))
    return None


def _pin_escape_global_dir(
    artifact: FrontendArtifact,
    ep: str,
    rotation_deg: float = 0.0,
) -> tuple[float, float]:
    """Return a unit (or zero) global-frame escape direction for endpoint ``ep``,
    given the host component's ``rotation_deg`` (default 0 for fixed pins
    where rotation is not yet known at SA init).
    """
    if "." not in ep:
        return (0.0, 0.0)
    comp_id, pin_id = ep.split(".", 1)
    comp = artifact.components.get(comp_id)
    if comp is None:
        return (0.0, 0.0)
    pad = next((p for p in comp.pads if p.pin == pin_id), None)
    if pad is None:
        return (0.0, 0.0)
    lx = pad.local_x or 0.0
    ly = pad.local_y or 0.0
    if abs(lx) < 1e-9 and abs(ly) < 1e-9:
        return (0.0, 0.0)
    if abs(lx) >= abs(ly):
        dx, dy = (1.0 if lx >= 0 else -1.0, 0.0)
    else:
        dx, dy = (0.0, 1.0 if ly >= 0 else -1.0)
    th = math.radians(float(rotation_deg))
    cos_t = round(math.cos(th))
    sin_t = round(math.sin(th))
    rx = dx * cos_t - dy * sin_t
    ry = dx * sin_t + dy * cos_t
    return (float(rx), float(ry))


def _escape_alignment_penalty(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
) -> float:
    """Penalise floating-pin rotations that point AWAY from their connected
    endpoints.  For each (floating_pin, other_endpoint) pair in a flex edge,
    we compute the dot product of (other - pin) with the pin's escape unit
    vector.  Negative dot products (other endpoint is "behind" the pin)
    accumulate their magnitude as a penalty.  This drives SA toward rotations
    where pin escape directions face the right way for the actual routing.
    """
    floating = set(state.keys())
    penalty = 0.0
    for edge in artifact.edges.values():
        conns = edge.connections or ()
        if not any(ep.split(".", 1)[0] in floating for ep in conns if "." in ep):
            continue
        # Pre-resolve all endpoint coordinates.
        ep_xy: dict[str, tuple[float, float]] = {}
        ep_dir: dict[str, tuple[float, float]] = {}
        for ep in conns:
            comp_id = ep.split(".", 1)[0] if "." in ep else None
            if comp_id and comp_id in floating:
                fp = state[comp_id]
                comp = artifact.components.get(comp_id)
                if comp is None:
                    continue
                pad = next(
                    (p for p in comp.pads if p.pin == ep.split(".", 1)[1]),
                    None,
                )
                lx = (pad.local_x if pad else 0.0) or 0.0
                ly = (pad.local_y if pad else 0.0) or 0.0
                th = math.radians(float(fp.rotation))
                c, s = round(math.cos(th)), round(math.sin(th))
                px = fp.x + lx * c - ly * s
                py = fp.y + lx * s + ly * c
                ep_xy[ep] = (px, py)
                ep_dir[ep] = _pin_escape_global_dir(artifact, ep, float(fp.rotation))
            else:
                xy = _resolve_endpoint(artifact, skeleton, adhesion, ep)
                if xy is not None:
                    ep_xy[ep] = xy
        for ep, (px, py) in ep_xy.items():
            d = ep_dir.get(ep, (0.0, 0.0))
            if d == (0.0, 0.0):
                continue
            for other_ep, (ox, oy) in ep_xy.items():
                if other_ep == ep:
                    continue
                vx, vy = ox - px, oy - py
                dot = vx * d[0] + vy * d[1]
                if dot < 0:
                    penalty += -dot
    return penalty


# ----------------------------------------------------------------------
# Geometric crossing helpers (Task 1 — segment intersection primitive)
# ----------------------------------------------------------------------


def _ccw(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    """Cross product (b-a) × (c-a); >0 ccw, <0 cw, =0 collinear."""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_segment(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float
) -> bool:
    """Is C on segment AB (assuming collinear)?"""
    return (min(ax, bx) - 1e-9 <= cx <= max(ax, bx) + 1e-9) and (
        min(ay, by) - 1e-9 <= cy <= max(ay, by) + 1e-9
    )


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
    *,
    ignore_shared_endpoints: bool = True,
) -> bool:
    """Standard CCW-based segment intersection.

    ``ignore_shared_endpoints=True``: if the two segments share an exact
    endpoint (within 1e-7), they are treated as non-intersecting (legal in
    PCB topology — pins of the same net touching is fine).  Collinear
    overlap (more than just a shared endpoint) still counts as intersection.
    """
    EPS = 1e-7
    if ignore_shared_endpoints:
        shared = 0
        for p in (a1, a2):
            for q in (b1, b2):
                if abs(p[0] - q[0]) < EPS and abs(p[1] - q[1]) < EPS:
                    shared += 1
        if shared >= 1:
            # Allow exactly one shared endpoint as a no-cross "touch".
            # Two shared endpoints means the segments are identical — count
            # as intersection (it's a degenerate overlap).
            if shared == 1:
                return False

    d1 = _ccw(b1[0], b1[1], b2[0], b2[1], a1[0], a1[1])
    d2 = _ccw(b1[0], b1[1], b2[0], b2[1], a2[0], a2[1])
    d3 = _ccw(a1[0], a1[1], a2[0], a2[1], b1[0], b1[1])
    d4 = _ccw(a1[0], a1[1], a2[0], a2[1], b2[0], b2[1])
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    # Collinear cases — overlap counts.
    if abs(d1) < 1e-12 and _on_segment(b1[0], b1[1], b2[0], b2[1], a1[0], a1[1]):
        return True
    if abs(d2) < 1e-12 and _on_segment(b1[0], b1[1], b2[0], b2[1], a2[0], a2[1]):
        return True
    if abs(d3) < 1e-12 and _on_segment(a1[0], a1[1], a2[0], a2[1], b1[0], b1[1]):
        return True
    if abs(d4) < 1e-12 and _on_segment(a1[0], a1[1], a2[0], a2[1], b2[0], b2[1]):
        return True
    return False


def _floating_endpoint_xy(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    ep: str,
) -> tuple[float, float] | None:
    """Resolve endpoint XY for crossing-cost terms.  For a floating pin,
    use the rotated pin position; for fixed/adhered, use the resolved pad
    centre."""
    if "." in ep:
        comp_id, pin_id = ep.split(".", 1)
        if comp_id in state:
            comp = artifact.components.get(comp_id)
            if comp is not None:
                xy = _floating_pin_xy(comp, pin_id, state[comp_id])
                if xy is not None:
                    return xy
            return (state[comp_id].x, state[comp_id].y)
    return _resolve_endpoint(artifact, skeleton, adhesion, ep)


def _floating_airwires(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
) -> list[tuple[tuple[float, float], tuple[float, float], str]]:
    """Return list of ((x1,y1), (x2,y2), edge_id) for every edge with at
    least one floating endpoint, using current SA state."""
    floating_names = set(state.keys())
    out: list[tuple[tuple[float, float], tuple[float, float], str]] = []
    for edge in artifact.edges.values():
        conns = edge.connections or ()
        if not any(ep.split(".", 1)[0] in floating_names for ep in conns if "." in ep):
            continue
        pts: list[tuple[float, float]] = []
        for ep in conns:
            xy = _floating_endpoint_xy(artifact, state, skeleton, adhesion, ep)
            if xy is not None:
                pts.append(xy)
        for p1, p2 in zip(pts, pts[1:]):
            if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-6:
                continue
            out.append((p1, p2, edge.name))
    return out


def _rf_bus_cross_cost(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
) -> float:
    """Count crossings between each floating-airwire and every locked RF
    route segment.  ``skeleton.routes`` are the already-routed RF traces
    (rf_constrained_locked etc.)."""
    airwires = _floating_airwires(artifact, state, skeleton, adhesion)
    if not airwires:
        return 0.0
    rf_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for route in skeleton.routes.values():
        if not route.success or len(route.polyline_um) < 2:
            continue
        pts = [(um_to_mm(x), um_to_mm(y)) for x, y in route.polyline_um]
        for p1, p2 in zip(pts, pts[1:]):
            rf_segments.append((p1, p2))
    if not rf_segments:
        return 0.0
    cost = 0.0
    for a1, a2, _eid in airwires:
        for b1, b2 in rf_segments:
            if _segments_intersect(a1, a2, b1, b2):
                cost += 1.0
    return cost


def _airwire_cross_cost(
    artifact: FrontendArtifact,
    state: dict[str, FloatingPlacement],
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
) -> float:
    """Count pairwise crossings between distinct floating airwires."""
    airwires = _floating_airwires(artifact, state, skeleton, adhesion)
    n = len(airwires)
    if n < 2:
        return 0.0
    cost = 0.0
    for i in range(n):
        a1, a2, _eid_i = airwires[i]
        for j in range(i + 1, n):
            b1, b2, _eid_j = airwires[j]
            if _segments_intersect(a1, a2, b1, b2):
                cost += 1.0
    return cost
