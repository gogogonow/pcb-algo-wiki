"""WI-J4 — Hard DRC for phaseC flexible routes.

提供两类硬约束违规检测：

* ``overlap``：flex polyline 与任意 obstacle bbox 相交（用线段 bbox + width/clearance
  与障碍 bbox 是否相交近似 — 适合 90/45 度走线的快速保守判定）
* ``crossing``：两条 polyline 的非共享线段相互相交（标准方向法）

orchestrator 检测到任意违规后，会把该 flex edge 标为 FAILED 并不写回
``GeometryIR.routes``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from schema.geometry_ir import RoutePolyline


@dataclass(frozen=True)
class DrcViolation:
    edge_id: str
    kind: str  # "overlap" | "crossing"
    detail: str


@dataclass
class DrcReport:
    violations: list[DrcViolation] = field(default_factory=list)

    def failed_edges(self) -> set[str]:
        return {v.edge_id for v in self.violations}


@dataclass(frozen=True)
class ObstacleEntry:
    """精细化障碍：把 component_body / pad / legacy 区别开。

    * ``kind="pad"``：``owner_id`` 是完整 pin id，如 ``"U_BIAS.PIN_2"``。
      DRC 时仅当 ``owner_id`` 等于路由两端任意端点 pin 时才豁免。
    * ``kind="component_body"``：``owner_id`` 是组件名，如 ``"U_BIAS"``。
      DRC 时若路由端点 pin 的组件名匹配则豁免（允许从组件本体内部出 pin）。
    * ``kind="legacy"``：旧逻辑，端点落入 bbox 即豁免整个 bbox。
    """

    bbox: tuple[float, float, float, float]
    kind: str
    owner_id: str | None = None


def _comp_of(pin_id: str | None) -> str | None:
    if pin_id is None:
        return None
    return pin_id.split(".", 1)[0]


def validate_flex_routes(
    *,
    flex_routes: Mapping[str, RoutePolyline],
    other_routes: Mapping[str, RoutePolyline],
    obstacle_bboxes: Iterable[tuple[float, float, float, float]] = (),
    obstacle_entries: Iterable[ObstacleEntry] = (),
    endpoint_pin_ids: Mapping[str, tuple[str, str]] | None = None,
    clearance_mm: float = 0.05,
    use_spatial_index: bool = True,
) -> DrcReport:
    report = DrcReport()
    legacy_obs: list[tuple[float, float, float, float]] = list(obstacle_bboxes)
    entries: list[ObstacleEntry] = list(obstacle_entries)
    pin_map: Mapping[str, tuple[str, str]] = endpoint_pin_ids or {}
    flex_items = list(flex_routes.items())

    # 1. polyline ↔ obstacle overlap with pad-granular exemption.
    for eid, route in flex_items:
        pts = route.points
        if len(pts) < 2:
            continue
        ep0 = (float(pts[0].x), float(pts[0].y))
        ep1 = (float(pts[-1].x), float(pts[-1].y))
        end_pins = pin_map.get(eid)
        end_pin_ids = set(end_pins) if end_pins else set()
        end_comp_names = {_comp_of(p) for p in end_pin_ids if p}

        # legacy: 端点落入 bbox 即整块豁免（保留旧行为给旧调用者）
        effective_legacy = [
            ob
            for ob in legacy_obs
            if not (_point_in_bbox(ep0, ob) or _point_in_bbox(ep1, ob))
        ]
        # entries: 按 kind/owner_id 精确豁免
        # 对于 endpoint 组件的同伴 pad（owner_id 同 component 但不同 pin），
        # 几何上无法保持完整 clearance（同件 pad 间距常 < 1.5mm），
        # 因此降级为 clearance=0（仅检查路由是否真的穿入 pad bbox）。
        sibling_pad_bboxes: list[tuple[float, float, float, float]] = []
        effective_entries: list[tuple[float, float, float, float]] = []
        for entry in entries:
            if entry.kind == "pad":
                if entry.owner_id in end_pin_ids:
                    continue
                # sibling pad of an endpoint component
                pad_comp = _comp_of(entry.owner_id)
                if pad_comp in end_comp_names:
                    sibling_pad_bboxes.append(entry.bbox)
                    continue
            elif entry.kind == "component_body":
                if entry.owner_id in end_comp_names:
                    continue
                # If the body bbox swallows either endpoint of this route
                # (e.g. SA placer placed a foreign component overlapping the
                # endpoint component), exempt it — penalising would otherwise
                # be physically unavoidable for that route.
                if _point_in_bbox(ep0, entry.bbox) or _point_in_bbox(ep1, entry.bbox):
                    continue
            elif entry.kind == "legacy":
                if _point_in_bbox(ep0, entry.bbox) or _point_in_bbox(ep1, entry.bbox):
                    continue
            effective_entries.append(entry.bbox)

        all_eff = effective_legacy + effective_entries
        obs_index = _build_bbox_bucket_index(all_eff) if use_spatial_index else None
        hit = _polyline_hits_obstacle(route, all_eff, clearance_mm, obs_index)
        if not hit and sibling_pad_bboxes:
            sibling_index = (
                _build_bbox_bucket_index(sibling_pad_bboxes)
                if use_spatial_index
                else None
            )
            hit = _polyline_hits_obstacle(route, sibling_pad_bboxes, 0.0, sibling_index)
        if hit:
            report.violations.append(
                DrcViolation(eid, "overlap", "segment bbox intersects obstacle")
            )

    # 2. polyline ↔ polyline crossings (flex vs flex / flex vs other)
    all_segs: list[
        tuple[
            str,
            tuple[float, float],
            tuple[float, float],
            tuple[float, float, float, float],
        ]
    ] = []
    for eid, route in flex_items:
        for a, b in _segments(route):
            all_segs.append((eid, a, b, _segment_bbox(a, b)))
    for eid, route in other_routes.items():
        for a, b in _segments(route):
            all_segs.append((eid, a, b, _segment_bbox(a, b)))

    crossed: set[str] = set()
    if use_spatial_index:
        candidate_pairs = _segment_candidate_pairs(all_segs)
    else:
        candidate_pairs = {
            (i, j) for i in range(len(all_segs)) for j in range(i + 1, len(all_segs))
        }

    for i, j in sorted(candidate_pairs):
        eid_a, a1, a2, bb_a = all_segs[i]
        eid_b, b1, b2, bb_b = all_segs[j]
        if eid_a == eid_b:
            continue
        if not _bbox_strict_overlap(bb_a, bb_b):
            continue
        if _segments_share_endpoint(a1, a2, b1, b2):
            continue
        if _segments_cross(a1, a2, b1, b2):
            if eid_a in flex_routes and eid_a not in crossed:
                report.violations.append(
                    DrcViolation(eid_a, "crossing", f"crosses {eid_b}")
                )
                crossed.add(eid_a)
            if eid_b in flex_routes and eid_b not in crossed:
                report.violations.append(
                    DrcViolation(eid_b, "crossing", f"crosses {eid_a}")
                )
                crossed.add(eid_b)
    return report


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _point_in_bbox(
    p: tuple[float, float], bb: tuple[float, float, float, float]
) -> bool:
    eps = 1e-6
    return bb[0] - eps <= p[0] <= bb[2] + eps and bb[1] - eps <= p[1] <= bb[3] + eps


def _polyline_hits_obstacle(
    route: RoutePolyline,
    obstacles: list[tuple[float, float, float, float]],
    clearance_mm: float,
    obstacle_index: dict[tuple[int, int], list[int]] | None = None,
) -> bool:
    half = float(route.width) / 2.0 + clearance_mm
    for (x1, y1), (x2, y2) in _segments(route):
        seg_bb = (
            min(x1, x2) - half,
            min(y1, y2) - half,
            max(x1, x2) + half,
            max(y1, y2) + half,
        )
        if obstacle_index is None:
            candidates = range(len(obstacles))
        else:
            candidates = _bbox_candidates(seg_bb, obstacle_index)
        for idx in candidates:
            ob = obstacles[idx]
            if _bbox_strict_overlap(seg_bb, ob):
                return True
    return False


def _segment_bbox(
    a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _build_bbox_bucket_index(
    bboxes: list[tuple[float, float, float, float]], bucket_mm: float = 2.0
) -> dict[tuple[int, int], list[int]]:
    index: dict[tuple[int, int], list[int]] = {}
    for idx, bb in enumerate(bboxes):
        for key in _bbox_bucket_keys(bb, bucket_mm):
            index.setdefault(key, []).append(idx)
    return index


def _bbox_bucket_keys(
    bb: tuple[float, float, float, float], bucket_mm: float
) -> list[tuple[int, int]]:
    x0 = int(bb[0] // bucket_mm)
    x1 = int(bb[2] // bucket_mm)
    y0 = int(bb[1] // bucket_mm)
    y1 = int(bb[3] // bucket_mm)
    return [(bx, by) for bx in range(x0, x1 + 1) for by in range(y0, y1 + 1)]


def _bbox_candidates(
    bb: tuple[float, float, float, float],
    index: dict[tuple[int, int], list[int]],
    bucket_mm: float = 2.0,
) -> set[int]:
    out: set[int] = set()
    for key in _bbox_bucket_keys(bb, bucket_mm):
        out.update(index.get(key, ()))
    return out


def _segment_candidate_pairs(
    segments: list[
        tuple[
            str,
            tuple[float, float],
            tuple[float, float],
            tuple[float, float, float, float],
        ]
    ],
    bucket_mm: float = 2.0,
) -> set[tuple[int, int]]:
    index: dict[tuple[int, int], list[int]] = {}
    for idx, (_, _, _, bb) in enumerate(segments):
        for key in _bbox_bucket_keys(bb, bucket_mm):
            index.setdefault(key, []).append(idx)
    out: set[tuple[int, int]] = set()
    for bucket_items in index.values():
        n = len(bucket_items)
        for i in range(n):
            for j in range(i + 1, n):
                a = bucket_items[i]
                b = bucket_items[j]
                if a < b:
                    out.add((a, b))
                else:
                    out.add((b, a))
    return out


def _segments(
    route: RoutePolyline,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    pts = [(float(p.x), float(p.y)) for p in route.points]
    return list(zip(pts, pts[1:]))


def _bbox_strict_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """严格相交（边贴边视为不交，避免端点接触误报）。"""
    eps = 1e-6
    return not (
        a[2] <= b[0] + eps
        or b[2] <= a[0] + eps
        or a[3] <= b[1] + eps
        or b[3] <= a[1] + eps
    )


def _orient(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> int:
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if v > 1e-9:
        return 1
    if v < -1e-9:
        return -1
    return 0


def _on_seg(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> bool:
    return (
        min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9
        and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9
    )


def _segments_cross(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    o1 = _orient(p1, p2, p3)
    o2 = _orient(p1, p2, p4)
    o3 = _orient(p3, p4, p1)
    o4 = _orient(p3, p4, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_seg(p1, p2, p3):
        return True
    if o2 == 0 and _on_seg(p1, p2, p4):
        return True
    if o3 == 0 and _on_seg(p3, p4, p1):
        return True
    if o4 == 0 and _on_seg(p3, p4, p2):
        return True
    return False


def _segments_share_endpoint(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    def _eq(p: tuple[float, float], q: tuple[float, float]) -> bool:
        return abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6

    return _eq(a1, b1) or _eq(a1, b2) or _eq(a2, b1) or _eq(a2, b2)
