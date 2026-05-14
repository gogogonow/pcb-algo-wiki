"""Post-extract audits for the M4 GeometryIR.

These run after CP-SAT solve to validate properties that we either chose not
to encode as hard CP-SAT constraints (like NoOverlap between disjoint route
bboxes — see ``cpsat.build_model`` step 7) or that need a regression-friendly
report (locked-edge length error percentages).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from schema.geometry_ir import GeometryIR
from schema.solver_ir import SolverIR


@dataclass(frozen=True)
class LengthCheck:
    edge_id: str
    target_length: float
    actual_length: float
    error_fraction: float


@dataclass(frozen=True)
class OverlapPair:
    edge_a: str
    edge_b: str
    overlap_um: int


@dataclass(frozen=True)
class AuditReport:
    length_checks: tuple[LengthCheck, ...]
    overlap_pairs: tuple[OverlapPair, ...]
    max_length_error_fraction: float
    locked_edges_within_tolerance: bool
    no_overlap_pass: bool

    @property
    def passed(self) -> bool:
        return self.locked_edges_within_tolerance and self.no_overlap_pass


def manhattan_length(geom: GeometryIR, edge_id: str) -> float:
    """Sum of Manhattan |dx|+|dy| over the polyline (legacy M4 behaviour)."""
    points = geom.routes[edge_id].points
    total = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        total += abs(a.x - b.x) + abs(a.y - b.y)
    return total


def polyline_length(geom: GeometryIR, edge_id: str) -> float:
    """Sum of Euclidean segment lengths over the polyline.

    M9 introduces 45° (octilinear) segments whose physical length is
    ``√2 × axis_step`` rather than ``2 × axis_step``. Length-lock auditing
    must therefore use Euclidean rather than Manhattan distance to remain
    consistent with the actual trace geometry.

    Backward compatibility: a *2-point* polyline is the legacy CP-SAT
    "two-endpoint" representation that implicitly stands for an axis-aligned
    L-shape whose Manhattan length matches the locked target. We honour that
    contract by reporting Manhattan length for 2-point polylines so M5–M8
    behaviour is preserved.
    """
    points = geom.routes[edge_id].points
    if len(points) <= 2:
        return manhattan_length(geom, edge_id)
    total = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        total += math.hypot(a.x - b.x, a.y - b.y)
    return total


def _bbox_um(
    points: tuple, width: float, clearance: float
) -> tuple[int, int, int, int]:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    half_w = width / 2.0 + clearance
    return (
        int(round((min(xs) - half_w) * 1000)),
        int(round((min(ys) - half_w) * 1000)),
        int(round((max(xs) + half_w) * 1000)),
        int(round((max(ys) + half_w) * 1000)),
    )


def audit_geometry(
    ir: SolverIR,
    geom: GeometryIR,
    *,
    length_tolerance_fraction: float = 0.005,
    skip_length_edges: frozenset[str] | set[str] | None = None,
    resolved_endpoints: dict[str, tuple[str, str]] | None = None,
) -> AuditReport:
    """Validate length constraints and inter-route NoOverlap in the geometry.

    ``resolved_endpoints`` (optional) maps edge_id → (ep_a, ep_b) using
    physical endpoint identifiers after branch_origin aliasing; when supplied,
    edges that share any physical endpoint are exempt from NoOverlap. Without
    it the audit falls back to ``ir.edges[*].endpoints`` which can flag false
    positives at universal_junction branch origins (M3 branch sub-edges have
    distinct symbolic endpoints that are co-located at the junction centre).
    """

    skip_length = set(skip_length_edges or ())
    length_checks: list[LengthCheck] = []
    max_err = 0.0
    all_within = True
    for edge_id, edge in ir.edges.items():
        if edge.routing_class.value != "rf_constrained_locked":
            continue
        if edge.target_length is None or edge_id not in geom.routes:
            continue
        if edge_id in skip_length:
            continue
        L = polyline_length(geom, edge_id)
        target = float(edge.target_length)
        err = (L - target) / target if target > 0 else 0.0
        length_checks.append(
            LengthCheck(
                edge_id=edge_id,
                target_length=target,
                actual_length=L,
                error_fraction=err,
            )
        )
        max_err = max(max_err, abs(err))
        if abs(err) > length_tolerance_fraction + 1e-9:
            all_within = False

    edge_ids = list(geom.routes)
    edge_endpoints: dict[str, set[str]] = {}
    for eid in edge_ids:
        if eid not in ir.edges:
            continue
        eps: set[str] = set(ir.edges[eid].endpoints)
        if resolved_endpoints is not None and eid in resolved_endpoints:
            eps.update(resolved_endpoints[eid])
        edge_endpoints[eid] = eps

    overlaps: list[OverlapPair] = []
    clearance = float(ir.clearance)
    for i, ea in enumerate(edge_ids):
        for eb in edge_ids[i + 1 :]:
            if ea not in edge_endpoints or eb not in edge_endpoints:
                continue
            if edge_endpoints[ea] & edge_endpoints[eb]:
                continue  # connected — share an endpoint
            ra, rb = geom.routes[ea], geom.routes[eb]
            ax_min, ay_min, ax_max, ay_max = _bbox_um(
                ra.points, float(ra.width), clearance
            )
            bx_min, by_min, bx_max, by_max = _bbox_um(
                rb.points, float(rb.width), clearance
            )
            ox = min(ax_max, bx_max) - max(ax_min, bx_min)
            oy = min(ay_max, by_max) - max(ay_min, by_min)
            if ox > 0 and oy > 0:
                overlaps.append(
                    OverlapPair(edge_a=ea, edge_b=eb, overlap_um=min(ox, oy))
                )

    return AuditReport(
        length_checks=tuple(length_checks),
        overlap_pairs=tuple(overlaps),
        max_length_error_fraction=max_err,
        locked_edges_within_tolerance=all_within,
        no_overlap_pass=not overlaps,
    )


__all__ = [
    "AuditReport",
    "LengthCheck",
    "OverlapPair",
    "audit_geometry",
    "manhattan_length",
    "polyline_length",
]
