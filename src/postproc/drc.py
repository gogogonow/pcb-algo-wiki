"""M6/M7 hard DRC — checks min_width / min_clearance / via_density.

M7 upgrade: Rule 2 (min_clearance) now uses true segment-segment minimum
distance instead of inflated bbox overlap.  This eliminates the large number
of false-positive warnings caused by diagonal segments whose bounding boxes
overlap even when the actual traces are well-separated.

PA single-layer YAML has no vias → ``via_density`` always passes; the field
is kept for forward compatibility with multi-layer/v7 work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schema.geometry_ir import GeometryIR
from schema.solver_ir import SolverIR

if TYPE_CHECKING:
    from schema.v6_ir import Point


@dataclass(frozen=True)
class DrcViolation:
    rule: str  # "min_width" | "min_clearance" | "via_density"
    severity: str  # "critical" | "warning"
    edge_a: str
    edge_b: str | None
    detail: str


@dataclass(frozen=True)
class DrcReport:
    min_trace_width: float
    min_clearance: float
    violations: tuple[DrcViolation, ...] = ()

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    @property
    def passed(self) -> bool:
        return self.critical_count == 0


def run_drc(
    geom: GeometryIR,
    ir: SolverIR,
    *,
    min_trace_width: float | None = None,
    min_clearance: float | None = None,
    known_overlap_pairs: (
        frozenset[tuple[str, str]] | set[tuple[str, str]] | None
    ) = None,
) -> DrcReport:
    """Run hard DRC against a (possibly bended) GeometryIR.

    ``min_trace_width`` defaults to the smallest configured route width across
    the SolverIR; ``min_clearance`` defaults to ``ir.clearance``.

    ``known_overlap_pairs`` lets callers waiver pre-existing audit-known
    overlap pairs (e.g. M5 CP-SAT BBox-NoOverlap shortfalls on diagonal
    segments) down to ``severity="warning"`` so M6 only escalates **new**
    post-bend critical breaches. Pairs are matched order-insensitively.
    """

    waivered = set()
    if known_overlap_pairs:
        for a, b in known_overlap_pairs:
            waivered.add((a, b))
            waivered.add((b, a))

    widths = [float(r.width) for r in geom.routes.values()]
    width_default = (
        min_trace_width
        if min_trace_width is not None
        else (min(widths) if widths else 0.0)
    )
    clearance_default = (
        float(min_clearance) if min_clearance is not None else float(ir.clearance)
    )

    violations: list[DrcViolation] = []

    # Rule 1 — min_width per route.
    for edge_id, route in geom.routes.items():
        if width_default > 0 and float(route.width) + 1e-9 < width_default:
            violations.append(
                DrcViolation(
                    rule="min_width",
                    severity="critical",
                    edge_a=edge_id,
                    edge_b=None,
                    detail=(
                        f"width={float(route.width):.4f}mm < "
                        f"min_trace_width={width_default:.4f}mm"
                    ),
                )
            )

    # Rule 2 — pairwise clearance (skip same-net or shared-endpoint pairs).
    edge_ids = list(geom.routes)
    edge_endpoints: dict[str, set[str]] = {}
    edge_nets: dict[str, str | None] = {}
    for eid in edge_ids:
        if eid not in ir.edges:
            continue
        edge_endpoints[eid] = set(ir.edges[eid].endpoints)
        edge_nets[eid] = ir.edges[eid].net

    for i, ea in enumerate(edge_ids):
        ra = geom.routes[ea]
        for eb in edge_ids[i + 1 :]:
            if ea in edge_endpoints and eb in edge_endpoints:
                if edge_endpoints[ea] & edge_endpoints[eb]:
                    continue
                net_a = edge_nets.get(ea)
                net_b = edge_nets.get(eb)
                if net_a is not None and net_a == net_b:
                    continue
            rb = geom.routes[eb]
            # Required edge-to-edge gap = half_width_a + half_width_b + clearance.
            required_gap = (
                float(ra.width) / 2.0 + float(rb.width) / 2.0 + clearance_default
            )
            min_dist = _polyline_polyline_min_dist(ra.points, rb.points)
            if min_dist < required_gap - 1e-6:
                severity = "warning" if (ea, eb) in waivered else "critical"
                gap_um = int(round(min_dist * 1000))
                req_um = int(round(required_gap * 1000))
                violations.append(
                    DrcViolation(
                        rule="min_clearance",
                        severity=severity,
                        edge_a=ea,
                        edge_b=eb,
                        detail=(
                            f"seg-seg distance {gap_um}µm < required {req_um}µm "
                            f"(trace_clearance={clearance_default:.4f}mm)"
                            + (" (audit-waivered)" if severity == "warning" else "")
                        ),
                    )
                )

    # Rule 3 — via_density (PA single-layer ⇒ no vias; reserved for v7).
    # Intentionally a no-op; if future GeometryIR carries vias, fold here.

    return DrcReport(
        min_trace_width=width_default,
        min_clearance=clearance_default,
        violations=tuple(violations),
    )


def _seg_seg_min_dist(p1: "Point", p2: "Point", p3: "Point", p4: "Point") -> float:
    """Return the minimum Euclidean distance between segment p1-p2 and segment p3-p4.

    Uses parametric closest-approach math; clamps to [0,1] for each segment.
    """
    dx1 = p2.x - p1.x
    dy1 = p2.y - p1.y
    dx2 = p4.x - p3.x
    dy2 = p4.y - p3.y
    dx12 = p1.x - p3.x
    dy12 = p1.y - p3.y

    a = dx1 * dx1 + dy1 * dy1  # |seg1|^2
    e = dx2 * dx2 + dy2 * dy2  # |seg2|^2
    f = dx2 * dx12 + dy2 * dy12

    # Degenerate: both points
    if a < 1e-12 and e < 1e-12:
        return math.hypot(p1.x - p3.x, p1.y - p3.y)
    if a < 1e-12:
        # seg1 is a point
        t = max(0.0, min(1.0, f / e))
        qx = p3.x + t * dx2
        qy = p3.y + t * dy2
        return math.hypot(p1.x - qx, p1.y - qy)

    c = dx1 * dx12 + dy1 * dy12
    if e < 1e-12:
        # seg2 is a point
        s = max(0.0, min(1.0, -c / a))
        px = p1.x + s * dx1
        py = p1.y + s * dy1
        return math.hypot(px - p3.x, py - p3.y)

    b = dx1 * dx2 + dy1 * dy2  # dot(d1, d2)
    denom = a * e - b * b

    if abs(denom) > 1e-12:
        s = max(0.0, min(1.0, (b * f - c * e) / denom))
    else:
        s = 0.0  # parallel segments — use s=0

    t = (b * s + f) / e
    if t < 0.0:
        t = 0.0
        s = max(0.0, min(1.0, -c / a))
    elif t > 1.0:
        t = 1.0
        s = max(0.0, min(1.0, (b - c) / a))

    px = p1.x + s * dx1
    py = p1.y + s * dy1
    qx = p3.x + t * dx2
    qy = p3.y + t * dy2
    return math.hypot(px - qx, py - qy)


def _polyline_polyline_min_dist(
    pts_a: "tuple[Point, ...]", pts_b: "tuple[Point, ...]"
) -> float:
    """Return the minimum segment-segment distance between two polylines."""
    min_d = float("inf")
    for i in range(len(pts_a) - 1):
        for j in range(len(pts_b) - 1):
            d = _seg_seg_min_dist(pts_a[i], pts_a[i + 1], pts_b[j], pts_b[j + 1])
            if d < min_d:
                min_d = d
                if min_d < 1e-9:
                    return min_d  # early exit if touching
    return min_d


def _inflate_bbox(
    points: tuple, width: float, clearance: float
) -> tuple[int, int, int, int]:
    """Legacy bbox helper — kept for unit-test backward compatibility only."""
    _BBOX_SCALE = 1000
    xs = [float(p.x) for p in points]
    ys = [float(p.y) for p in points]
    half = width / 2.0 + clearance
    return (
        int(round((min(xs) - half) * _BBOX_SCALE)),
        int(round((min(ys) - half) * _BBOX_SCALE)),
        int(round((max(xs) + half) * _BBOX_SCALE)),
        int(round((max(ys) + half) * _BBOX_SCALE)),
    )


__all__ = [
    "DrcReport",
    "DrcViolation",
    "_inflate_bbox",
    "_polyline_polyline_min_dist",
    "_seg_seg_min_dist",
    "run_drc",
]
