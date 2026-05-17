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
    iterations: int = 4000
    temperature_init: float = 5000.0
    temperature_min: float = 1.0
    cooling: float = 0.998
    step_mm: float = 1.2
    boundary_weight: float = 400.0
    overlap_weight: float = 2000.0
    hpwl_weight: float = 1.0
    clearance_mm: float = 0.2
    margin_mm: float = 0.5  # 与板框、其他障碍的最小距离裕量
    seed: int = 0xC0FFEE


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

    # 初始化：在 board 右上角分散摆放，避免与左下的 IC1 / 微带线集中区域冲突
    state: dict[str, FloatingPlacement] = {}
    for i, name in enumerate(floating_names):
        # 网格化散布在 board 中心右侧
        col = i % 2
        row = i // 2
        x0 = board_w * 0.65 + col * 6.0
        y0 = board_h * 0.55 + row * 6.0
        # 钳制到板内
        comp = artifact.components[name]
        w, h = _comp_wh(comp, rotation=0)
        x0 = min(max(x0, w / 2 + cfg.margin_mm), board_w - w / 2 - cfg.margin_mm)
        y0 = min(max(y0, h / 2 + cfg.margin_mm), board_h - h / 2 - cfg.margin_mm)
        state[name] = FloatingPlacement(x=x0, y=y0, rotation=0)

    def energy(s: dict[str, FloatingPlacement]) -> float:
        e = 0.0
        bboxes = [_bbox_for(artifact, n, p) for n, p in s.items()]
        for bb in bboxes:
            e += cfg.boundary_weight * _out_of_board_sq(
                bb, board_w, board_h, cfg.margin_mm
            )
            for ob in obstacles:
                e += cfg.overlap_weight * _bbox_overlap_area_sq(bb, ob)
        for i in range(len(bboxes)):
            for j in range(i + 1, len(bboxes)):
                e += cfg.overlap_weight * _bbox_overlap_area_sq(bboxes[i], bboxes[j])
        e += cfg.hpwl_weight * _hpwl_energy(artifact, s, skeleton, adhesion)
        return e

    cur_e = energy(state)
    best_state, best_e = dict(state), cur_e
    T = cfg.temperature_init

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
                best_state, best_e = dict(state), cur_e
        else:
            state[name] = old
        T *= cfg.cooling

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
    # 加一点 padding（焊盘半尺寸 ~ 0.5mm 兜底）
    pad = 0.5
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


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
