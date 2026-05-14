"""M8 crossing diagnostics — three-layer overlap analysis.

Layer 1: per-pair facts (geometry + topology classification).
Layer 2: root-cause aggregation (R1–R5) + region hotspots.
Layer 3: algorithmic GAP detection (6 built-in rules) with priority-ordered
fix recommendations.

The module is pure computation — it does not run the solver, render SVG, or
perform I/O. Inputs are :class:`GeometryIR`, :class:`SolverIR`, and the
``overlap_pairs`` produced by :mod:`solver.audit`. Output is a fully
populated :class:`CrossingReport` ready for serialisation by
:mod:`tools.crossing_report`.

Design notes
------------
* Root-cause classification (R1 first match wins): R1 shared-endpoint fanout
  → R2 same-net topology → R3 cross-net intersection → R4 long-vs-short
  intersection → R5 locked-vs-locked default.
* Hotspot clustering uses a deterministic 5×5 grid bucket on the board
  bbox; no external dependencies. A bucket containing ≥ 20% of total pairs
  is flagged; adjacent flagged buckets merge into a single hotspot bbox.
* GAP detectors are independent functions with explicit triggers; their
  ``estimated_pairs_eliminated`` figures are conservative heuristics
  (constants centralised at the top of this module for tuning).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverIR

# ---------------------------------------------------------------------------
# Tunable constants (intentionally centralised for future calibration).
# ---------------------------------------------------------------------------

CRITICAL_OVERLAP_UM = 1000  # > 1 mm overlap counted as "critical"
HOTSPOT_GRID_DIM = 5  # 5 × 5 buckets over the board bbox
HOTSPOT_MIN_FRACTION = 0.20  # bucket flagged if it holds ≥ 20% of pairs

# Per-class elimination yields used by GAP detectors; conservative.
YIELD_R1_FANOUT = 0.6
YIELD_PLACEMENT_HOTSPOT = 0.4
YIELD_FLEX_OBSTACLE = 1.0
YIELD_MEANDER = 0.7
YIELD_DATA_OVERSPEC = 1.0

COMPLEXITY_WEIGHT = {"S": 1, "M": 3, "L": 8}

# Root-cause string labels.
R1 = "R1_shared_pin_fanout"
R2 = "R2_same_net_join"
R3 = "R3_cross_net_traversal"
R4 = "R4_long_over_short"
R5 = "R5_locked_locked_collision"

ROOT_CAUSE_DESCRIPTIONS_ZH = {
    R1: "同源管脚扇出",
    R2: "同网络汇聚",
    R3: "异网络穿越",
    R4: "长边穿短边",
    R5: "锁长冲突",
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairFact:
    edge_a: str
    edge_b: str
    overlap_um: int
    overlap_area_um2: int
    nearest_distance_um: int
    intersection_angle_deg: float
    edge_a_class: str
    edge_b_class: str
    edge_a_length_mm: float
    edge_b_length_mm: float
    shared_endpoint: str | None
    same_net: bool
    root_cause: str
    centre_x_mm: float
    centre_y_mm: float


@dataclass(frozen=True)
class Hotspot:
    bbox_mm: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    contained_pair_count: int


@dataclass(frozen=True)
class AlgorithmicGap:
    gap_id: str
    gap_title: str
    affected_pair_count: int
    affected_fraction: float
    evidence: str
    root_cause_layer: str
    recommended_fix: str
    estimated_pairs_eliminated: int
    complexity: str
    priority_score: float


@dataclass(frozen=True)
class CrossingReport:
    project: str
    total_pairs: int
    critical_pair_count: int
    pair_facts: tuple[PairFact, ...]
    root_cause_counts: dict[str, int]
    region_hotspots: tuple[Hotspot, ...]
    gaps: tuple[AlgorithmicGap, ...]


@dataclass
class _AnalysisContext:
    """Internal scratch passed between helpers and detectors."""

    geom: GeometryIR
    solver_ir: SolverIR
    pair_facts: list[PairFact] = field(default_factory=list)
    root_cause_counts: dict[str, int] = field(default_factory=dict)
    hotspots: list[Hotspot] = field(default_factory=list)
    semantic_issue_count: int = 0  # G1+G4 indicator from caller


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _polyline_length_mm(route: RoutePolyline) -> float:
    pts = route.points
    total = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        total += math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))
    return total


def _polyline_centre(route: RoutePolyline) -> tuple[float, float]:
    pts = route.points
    xs = [float(p.x) for p in pts]
    ys = [float(p.y) for p in pts]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _segments(route: RoutePolyline) -> list[tuple[float, float, float, float]]:
    pts = route.points
    out: list[tuple[float, float, float, float]] = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        out.append((float(a.x), float(a.y), float(b.x), float(b.y)))
    return out


def _seg_seg_min_dist(
    p1x: float,
    p1y: float,
    p2x: float,
    p2y: float,
    p3x: float,
    p3y: float,
    p4x: float,
    p4y: float,
) -> float:
    """Min distance (mm) between segment p1-p2 and p3-p4 (parametric clamp)."""

    dx1, dy1 = p2x - p1x, p2y - p1y
    dx2, dy2 = p4x - p3x, p4y - p3y
    rx, ry = p1x - p3x, p1y - p3y
    a = dx1 * dx1 + dy1 * dy1
    e = dx2 * dx2 + dy2 * dy2
    f = dx2 * rx + dy2 * ry
    if a <= 1e-12 and e <= 1e-12:
        return math.hypot(rx, ry)
    if a <= 1e-12:
        s = 0.0
        t = max(0.0, min(1.0, f / e))
    else:
        c = dx1 * rx + dy1 * ry
        if e <= 1e-12:
            t = 0.0
            s = max(0.0, min(1.0, -c / a))
        else:
            b = dx1 * dx2 + dy1 * dy2
            denom = a * e - b * b
            s = 0.0 if denom == 0.0 else max(0.0, min(1.0, (b * f - c * e) / denom))
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t = 1.0
                s = max(0.0, min(1.0, (b - c) / a))
    cx1 = p1x + dx1 * s
    cy1 = p1y + dy1 * s
    cx2 = p3x + dx2 * t
    cy2 = p3y + dy2 * t
    return math.hypot(cx2 - cx1, cy2 - cy1)


def _polyline_polyline_min_dist_mm(a: RoutePolyline, b: RoutePolyline) -> float:
    sa = _segments(a)
    sb = _segments(b)
    if not sa or not sb:
        return float("inf")
    best = float("inf")
    for x1, y1, x2, y2 in sa:
        for x3, y3, x4, y4 in sb:
            d = _seg_seg_min_dist(x1, y1, x2, y2, x3, y3, x4, y4)
            if d < best:
                best = d
    return best


def _dominant_segment(route: RoutePolyline) -> tuple[float, float]:
    """Return the unit vector of the longest segment of the polyline."""

    longest_dx, longest_dy, longest_len = 1.0, 0.0, 0.0
    for x1, y1, x2, y2 in _segments(route):
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L > longest_len:
            longest_len = L
            longest_dx, longest_dy = dx, dy
    if longest_len <= 1e-9:
        return (1.0, 0.0)
    return (longest_dx / longest_len, longest_dy / longest_len)


def _intersection_angle_deg(a: RoutePolyline, b: RoutePolyline) -> float:
    ux, uy = _dominant_segment(a)
    vx, vy = _dominant_segment(b)
    dot = max(-1.0, min(1.0, ux * vx + uy * vy))
    raw = math.degrees(math.acos(abs(dot)))
    return raw  # in [0, 90]


def _bbox_overlap_area_um2(a: RoutePolyline, b: RoutePolyline) -> int:
    def _bbox(r: RoutePolyline) -> tuple[float, float, float, float]:
        xs = [float(p.x) for p in r.points]
        ys = [float(p.y) for p in r.points]
        half = float(r.width) / 2.0
        return (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)

    ax0, ay0, ax1, ay1 = _bbox(a)
    bx0, by0, bx1, by1 = _bbox(b)
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    return int(round(ix * iy * 1_000_000))  # mm² → µm²


# ---------------------------------------------------------------------------
# Root cause classification
# ---------------------------------------------------------------------------


def _classify_pair(
    edge_a: str,
    edge_b: str,
    geom: GeometryIR,
    solver_ir: SolverIR,
    angle_deg: float,
) -> tuple[str, str | None, bool]:
    """Return (root_cause, shared_endpoint, same_net)."""

    se_a = solver_ir.edges.get(edge_a)
    se_b = solver_ir.edges.get(edge_b)
    ep_a = set(se_a.endpoints) if se_a is not None else set()
    ep_b = set(se_b.endpoints) if se_b is not None else set()
    shared = sorted(ep_a & ep_b)
    shared_endpoint = shared[0] if shared else None

    net_a = se_a.net if se_a is not None else None
    net_b = se_b.net if se_b is not None else None
    same_net = net_a is not None and net_a == net_b

    # R1: shared endpoint = fanout from same physical pin.
    if shared_endpoint is not None:
        return (R1, shared_endpoint, same_net)

    # R2: same net but no shared physical endpoint.
    if same_net:
        return (R2, None, True)

    # Below: cross-net pairs.
    ra = geom.routes.get(edge_a)
    rb = geom.routes.get(edge_b)
    len_a = _polyline_length_mm(ra) if ra is not None else 0.0
    len_b = _polyline_length_mm(rb) if rb is not None else 0.0
    long_short = max(len_a, len_b) >= 3.0 * max(min(len_a, len_b), 1e-6)

    cls_a = se_a.routing_class if se_a is not None else None
    cls_b = se_b.routing_class if se_b is not None else None
    both_locked = (
        cls_a is RoutingClass.RF_CONSTRAINED_LOCKED
        and cls_b is RoutingClass.RF_CONSTRAINED_LOCKED
    )

    # R3: cross-net traversal at a meaningful angle.
    if angle_deg > 30.0:
        # R4 takes priority over R3 when length asymmetry is dramatic.
        if long_short:
            return (R4, None, False)
        return (R3, None, False)

    # R5: low-angle locked-locked default.
    if both_locked:
        return (R5, None, False)

    # Fallback: long-vs-short or generic R3.
    if long_short:
        return (R4, None, False)
    return (R3, None, False)


# ---------------------------------------------------------------------------
# Hotspot detection
# ---------------------------------------------------------------------------


def _compute_hotspots(
    pair_facts: Sequence[PairFact],
    geom: GeometryIR,
) -> list[Hotspot]:
    if not pair_facts:
        return []
    bw = float(geom.board.width)
    bh = float(geom.board.height)
    if bw <= 0.0 or bh <= 0.0:
        return []
    cell_w = bw / HOTSPOT_GRID_DIM
    cell_h = bh / HOTSPOT_GRID_DIM
    counts: dict[tuple[int, int], int] = {}
    for f in pair_facts:
        ix = max(0, min(HOTSPOT_GRID_DIM - 1, int(f.centre_x_mm / cell_w)))
        iy = max(0, min(HOTSPOT_GRID_DIM - 1, int(f.centre_y_mm / cell_h)))
        counts[(ix, iy)] = counts.get((ix, iy), 0) + 1

    threshold = max(1, math.ceil(len(pair_facts) * HOTSPOT_MIN_FRACTION))
    hot_cells = {c for c, n in counts.items() if n >= threshold}
    if not hot_cells:
        return []

    # Flood-fill merge adjacent (4-neighbour) hot cells.
    visited: set[tuple[int, int]] = set()
    hotspots: list[Hotspot] = []
    for cell in hot_cells:
        if cell in visited:
            continue
        stack = [cell]
        cluster = []
        while stack:
            c = stack.pop()
            if c in visited or c not in hot_cells:
                continue
            visited.add(c)
            cluster.append(c)
            cx, cy = c
            stack.extend([(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)])
        ixs = [c[0] for c in cluster]
        iys = [c[1] for c in cluster]
        bbox = (
            min(ixs) * cell_w,
            min(iys) * cell_h,
            (max(ixs) + 1) * cell_w,
            (max(iys) + 1) * cell_h,
        )
        contained = sum(counts[c] for c in cluster)
        hotspots.append(Hotspot(bbox_mm=bbox, contained_pair_count=contained))
    hotspots.sort(key=lambda h: -h.contained_pair_count)
    return hotspots


# ---------------------------------------------------------------------------
# GAP detectors (each independent, returns Optional[AlgorithmicGap])
# ---------------------------------------------------------------------------


def _make_gap(
    gap_id: str,
    title: str,
    affected: int,
    total: int,
    evidence: str,
    layer: str,
    fix: str,
    eliminated: int,
    complexity: str,
) -> AlgorithmicGap:
    fraction = (affected / total) if total > 0 else 0.0
    weight = COMPLEXITY_WEIGHT.get(complexity, 1)
    score = eliminated / weight
    return AlgorithmicGap(
        gap_id=gap_id,
        gap_title=title,
        affected_pair_count=affected,
        affected_fraction=fraction,
        evidence=evidence,
        root_cause_layer=layer,
        recommended_fix=fix,
        estimated_pairs_eliminated=eliminated,
        complexity=complexity,
        priority_score=score,
    )


def _detect_gap_cpsat_no_geom_freedom(ctx: _AnalysisContext) -> AlgorithmicGap | None:
    total = len(ctx.pair_facts)
    if total == 0:
        return None
    r5 = ctx.root_cause_counts.get(R5, 0)
    if r5 / total < 0.30:
        return None
    return _make_gap(
        gap_id="GAP-CPSAT-NO-GEOM-FREEDOM",
        title="CP-SAT 缺少 RF 锁长边的几何避让自由度",
        affected=r5,
        total=total,
        evidence=(
            f"R5（锁长冲突）占 {r5}/{total} 对（{r5/total*100:.0f}%）；"
            "所有受影响边均为单段直线 RF_CONSTRAINED_LOCKED，CP-SAT 未引入拐点变量。"
        ),
        layer="cpsat_routing",
        fix="M9 候选：Negotiated A* router + meander 兜底补长（grid 0.1mm + ripup-and-reroute）",
        eliminated=r5,
        complexity="L",
    )


def _detect_gap_placement_density(ctx: _AnalysisContext) -> AlgorithmicGap | None:
    total = len(ctx.pair_facts)
    if total == 0 or not ctx.hotspots:
        return None
    in_hotspots = sum(h.contained_pair_count for h in ctx.hotspots)
    if in_hotspots / total < 0.30:
        return None
    eliminated = int(round(in_hotspots * YIELD_PLACEMENT_HOTSPOT))
    return _make_gap(
        gap_id="GAP-PLACEMENT-DENSITY",
        title="布局密度过高：交叉集中在小区域",
        affected=in_hotspots,
        total=total,
        evidence=(
            f"{len(ctx.hotspots)} 个热点区域包含 {in_hotspots}/{total} 对重叠 "
            f"（{in_hotspots/total*100:.0f}%）；最热点 bbox={ctx.hotspots[0].bbox_mm}"
        ),
        layer="placement",
        fix="SA cost 增加分散项 / 调整 footprint margin / 评估增大板尺寸",
        eliminated=eliminated,
        complexity="M",
    )


def _detect_gap_fanout_not_dispersed(ctx: _AnalysisContext) -> AlgorithmicGap | None:
    total = len(ctx.pair_facts)
    if total == 0:
        return None
    r1 = ctx.root_cause_counts.get(R1, 0)
    if r1 / total < 0.20:
        return None
    eliminated = int(round(r1 * YIELD_R1_FANOUT))
    # Identify the most-shared pin for evidence.
    shared_counts: dict[str, int] = {}
    for f in ctx.pair_facts:
        if f.shared_endpoint is not None:
            shared_counts[f.shared_endpoint] = (
                shared_counts.get(f.shared_endpoint, 0) + 1
            )
    top = sorted(shared_counts.items(), key=lambda kv: -kv[1])[:3]
    top_repr = ", ".join(f"{pin}×{n}" for pin, n in top)
    return _make_gap(
        gap_id="GAP-FANOUT-NOT-DISPERSED",
        title="管脚扇出未分散：同 pin 多边互交",
        affected=r1,
        total=total,
        evidence=(
            f"R1（同源管脚扇出）占 {r1}/{total} 对（{r1/total*100:.0f}%）；"
            f"top 共享端点：{top_repr or '(none)'}"
        ),
        layer="placement",
        fix="管脚扇出策略：星形/树状路径，短引线段优先布外圈",
        eliminated=eliminated,
        complexity="M",
    )


def _detect_gap_flex_no_obstacle(ctx: _AnalysisContext) -> AlgorithmicGap | None:
    total = len(ctx.pair_facts)
    if total == 0:
        return None
    flex_class = "flexible_path"
    affected = 0
    for f in ctx.pair_facts:
        if f.root_cause not in (R3, R4):
            continue
        if flex_class in (f.edge_a_class, f.edge_b_class):
            affected += 1
    if affected == 0:
        return None
    eliminated = int(round(affected * YIELD_FLEX_OBSTACLE))
    return _make_gap(
        gap_id="GAP-FLEX-NO-OBSTACLE",
        title="A* flexible_path 障碍图缺已布 RF 边",
        affected=affected,
        total=total,
        evidence=f"{affected}/{total} 对涉及 flexible_path 边的异网络穿越",
        layer="astar",
        fix="A* obstacle map 补充已路由 RF 边的 inflated bbox",
        eliminated=eliminated,
        complexity="S",
    )


def _detect_gap_meander_collision(ctx: _AnalysisContext) -> AlgorithmicGap | None:
    total = len(ctx.pair_facts)
    if total == 0:
        return None

    # Heuristic: meander-affected edges manifest as polylines with >= 6 points
    # (M7 hairpin loops produce 4 extra points per loop). We treat any pair
    # involving such an edge as potential meander collision.
    def _is_meandered(edge_id: str) -> bool:
        r = ctx.geom.routes.get(edge_id)
        return r is not None and len(r.points) >= 6

    affected = 0
    for f in ctx.pair_facts:
        if f.root_cause not in (R3, R4):
            continue
        if _is_meandered(f.edge_a) or _is_meandered(f.edge_b):
            affected += 1
    if affected == 0:
        return None
    eliminated = int(round(affected * YIELD_MEANDER))
    return _make_gap(
        gap_id="GAP-MEANDER-COLLISION",
        title="蛇形走线引入新的几何冲突",
        affected=affected,
        total=total,
        evidence=f"{affected}/{total} 对涉及蛇形走线（≥6 点折线）的异网络穿越",
        layer="postproc",
        fix="meander 选段时跳过拥挤区，或 meander 后再做避障 pass",
        eliminated=eliminated,
        complexity="M",
    )


def _detect_gap_data_overspec(ctx: _AnalysisContext) -> AlgorithmicGap | None:
    total = len(ctx.pair_facts)
    if total == 0 or ctx.semantic_issue_count == 0:
        return None
    affected = ctx.semantic_issue_count
    eliminated = int(round(min(affected, total) * YIELD_DATA_OVERSPEC))
    return _make_gap(
        gap_id="GAP-DATA-OVERSPEC",
        title="YAML 数据过度约束诱发交叉",
        affected=affected,
        total=total,
        evidence=(
            f"semantic lint 报告 {affected} 条 length_infeasible_short / meander_required；"
            "数据约束可能与拓扑冲突"
        ),
        layer="data",
        fix="复核 target_length 与 placement；放松不可达约束",
        eliminated=eliminated,
        complexity="S",
    )


_GAP_DETECTORS = (
    _detect_gap_cpsat_no_geom_freedom,
    _detect_gap_placement_density,
    _detect_gap_fanout_not_dispersed,
    _detect_gap_flex_no_obstacle,
    _detect_gap_meander_collision,
    _detect_gap_data_overspec,
)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def analyze_crossings(
    geom: GeometryIR,
    solver_ir: SolverIR,
    overlap_pairs: Sequence,
    *,
    semantic_issue_count: int = 0,
) -> CrossingReport:
    """Build a complete :class:`CrossingReport` from solve outputs.

    ``overlap_pairs`` may contain :class:`solver.audit.OverlapPair` instances,
    plain dicts, or any object exposing ``edge_a``/``edge_b``/``overlap_um``
    attributes (or matching dict keys).

    ``semantic_issue_count`` is the number of frontend semantic lint findings
    (``length_infeasible_short`` + ``meander_required``); used by
    GAP-DATA-OVERSPEC.
    """

    ctx = _AnalysisContext(
        geom=geom, solver_ir=solver_ir, semantic_issue_count=semantic_issue_count
    )

    for raw in overlap_pairs:
        edge_a, edge_b, overlap_um = _coerce_overlap(raw)
        ra = geom.routes.get(edge_a)
        rb = geom.routes.get(edge_b)
        if ra is None or rb is None:
            continue
        len_a = _polyline_length_mm(ra)
        len_b = _polyline_length_mm(rb)
        angle = _intersection_angle_deg(ra, rb)
        root_cause, shared_ep, same_net = _classify_pair(
            edge_a, edge_b, geom, solver_ir, angle
        )
        cax, cay = _polyline_centre(ra)
        cbx, cby = _polyline_centre(rb)
        centre_x = (cax + cbx) / 2.0
        centre_y = (cay + cby) / 2.0
        nearest_um = int(round(_polyline_polyline_min_dist_mm(ra, rb) * 1000))
        area_um2 = _bbox_overlap_area_um2(ra, rb)
        cls_a = (
            solver_ir.edges[edge_a].routing_class.value
            if edge_a in solver_ir.edges
            else "unknown"
        )
        cls_b = (
            solver_ir.edges[edge_b].routing_class.value
            if edge_b in solver_ir.edges
            else "unknown"
        )
        ctx.pair_facts.append(
            PairFact(
                edge_a=edge_a,
                edge_b=edge_b,
                overlap_um=overlap_um,
                overlap_area_um2=area_um2,
                nearest_distance_um=nearest_um,
                intersection_angle_deg=angle,
                edge_a_class=cls_a,
                edge_b_class=cls_b,
                edge_a_length_mm=len_a,
                edge_b_length_mm=len_b,
                shared_endpoint=shared_ep,
                same_net=same_net,
                root_cause=root_cause,
                centre_x_mm=centre_x,
                centre_y_mm=centre_y,
            )
        )
        ctx.root_cause_counts[root_cause] = ctx.root_cause_counts.get(root_cause, 0) + 1

    ctx.hotspots = _compute_hotspots(ctx.pair_facts, geom)

    gaps: list[AlgorithmicGap] = []
    for det in _GAP_DETECTORS:
        gap = det(ctx)
        if gap is not None:
            gaps.append(gap)
    gaps.sort(key=lambda g: -g.priority_score)

    critical = sum(1 for f in ctx.pair_facts if f.overlap_um >= CRITICAL_OVERLAP_UM)
    return CrossingReport(
        project=geom.project,
        total_pairs=len(ctx.pair_facts),
        critical_pair_count=critical,
        pair_facts=tuple(ctx.pair_facts),
        root_cause_counts=dict(ctx.root_cause_counts),
        region_hotspots=tuple(ctx.hotspots),
        gaps=tuple(gaps),
    )


def _coerce_overlap(raw) -> tuple[str, str, int]:  # type: ignore[no-untyped-def]
    if isinstance(raw, dict):
        return (str(raw["edge_a"]), str(raw["edge_b"]), int(raw["overlap_um"]))
    return (str(raw.edge_a), str(raw.edge_b), int(raw.overlap_um))


__all__ = [
    "AlgorithmicGap",
    "CRITICAL_OVERLAP_UM",
    "CrossingReport",
    "Hotspot",
    "PairFact",
    "ROOT_CAUSE_DESCRIPTIONS_ZH",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "analyze_crossings",
]
