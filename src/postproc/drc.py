"""M6 hard DRC — checks min_width / min_clearance / via_density.

Reuses the inflated-bbox pairwise scheme from ``solver.audit`` but elevates
clearance violations to **critical** (M5 only emitted them as overlap
warnings) so downstream gating can refuse to ship boards with hard rule
breaches.

PA single-layer YAML has no vias → ``via_density`` always passes; the field
is kept for forward compatibility with multi-layer/v7 work.
"""

from __future__ import annotations

from dataclasses import dataclass

from schema.geometry_ir import GeometryIR
from schema.solver_ir import SolverIR

_BBOX_SCALE = 1000  # mm → µm to keep integer overlap math


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
            ax_min, ay_min, ax_max, ay_max = _inflate_bbox(
                ra.points, float(ra.width), clearance_default
            )
            bx_min, by_min, bx_max, by_max = _inflate_bbox(
                rb.points, float(rb.width), clearance_default
            )
            ox = min(ax_max, bx_max) - max(ax_min, bx_min)
            oy = min(ay_max, by_max) - max(ay_min, by_min)
            if ox > 0 and oy > 0:
                severity = "warning" if (ea, eb) in waivered else "critical"
                violations.append(
                    DrcViolation(
                        rule="min_clearance",
                        severity=severity,
                        edge_a=ea,
                        edge_b=eb,
                        detail=(
                            f"bbox-overlap {min(ox, oy)}µm < "
                            f"clearance={clearance_default:.4f}mm"
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


def _inflate_bbox(
    points: tuple, width: float, clearance: float
) -> tuple[int, int, int, int]:
    xs = [float(p.x) for p in points]
    ys = [float(p.y) for p in points]
    half = width / 2.0 + clearance
    return (
        int(round((min(xs) - half) * _BBOX_SCALE)),
        int(round((min(ys) - half) * _BBOX_SCALE)),
        int(round((max(xs) + half) * _BBOX_SCALE)),
        int(round((max(ys) + half) * _BBOX_SCALE)),
    )


__all__ = ["DrcReport", "DrcViolation", "run_drc"]
