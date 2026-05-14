"""M7 meander routing — add U-shaped serpentine loops to meet target_length.

When a route's ``target_length`` exceeds the actual routed length, this module
inserts U-shaped loops along the trace to make up the difference.  The routing
stays on the same layer; the insertion region is the middle 60% of the route so
that pin-escape transitions are preserved.

Algorithm (U-shaped meander):
1. Compute actual route length from the polyline points.
2. excess  = target_length - actual_length
3. Loop height h = max(excess / (2 * n_min_loops), width * 2 + spacing)
4. n_loops = ceil(excess / (2 * h))  → iterate until converged
5. Insert ``n_loops`` rectangular hairpin loops along the longest segment
   within the middle-60% insertion window.

Output is a new ``GeometryIR`` with replaced polylines plus a ``MeanderReport``
with per-edge statistics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import SolverIR
from schema.v6_ir import Point

# --------------------------------------------------------------------------- #
# Public types                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MeanderEdgeResult:
    edge_id: str
    original_length_mm: float
    target_length_mm: float
    achieved_length_mm: float
    n_loops: int
    loop_height_mm: float
    skipped: bool = False
    skip_reason: str = ""


@dataclass(frozen=True)
class MeanderReport:
    meandered_edges: tuple[MeanderEdgeResult, ...] = ()
    skipped_edges: tuple[MeanderEdgeResult, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def total_meandered(self) -> int:
        return len(self.meandered_edges)


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

_MIN_LOOP_SPACING_DEFAULT = 0.15  # mm — minimum clearance between loop legs


def _seg_length(p1: Point, p2: Point) -> float:
    return math.hypot(p2.x - p1.x, p2.y - p1.y)


def _polyline_length(points: tuple[Point, ...]) -> float:
    return sum(_seg_length(points[i], points[i + 1]) for i in range(len(points) - 1))


def _unit_vec(p1: Point, p2: Point) -> tuple[float, float]:
    dx, dy = p2.x - p1.x, p2.y - p1.y
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return 1.0, 0.0
    return dx / d, dy / d


def _perp(ux: float, uy: float) -> tuple[float, float]:
    """90° counter-clockwise perpendicular."""
    return -uy, ux


def _pt(x: float, y: float) -> Point:
    return Point(x=float(round(x, 6)), y=float(round(y, 6)))


def _insert_hairpin_loops(
    points: tuple[Point, ...],
    seg_idx: int,
    n_loops: int,
    loop_height: float,
    trace_width: float,
    spacing: float,
) -> tuple[Point, ...]:
    """Insert ``n_loops`` U-shaped hairpin loops into segment ``seg_idx``.

    Each loop is a rectangular detour perpendicular to the trace direction.
    Loops are evenly spaced within the middle-60% of the chosen segment.
    """
    p_start = points[seg_idx]
    p_end = points[seg_idx + 1]
    seg_len = _seg_length(p_start, p_end)

    ux, uy = _unit_vec(p_start, p_end)
    px, py = _perp(ux, uy)

    # Each loop consumes (trace_width + spacing) of width on the perp axis,
    # and ~0 extra longitudinal length (the two legs go there-and-back).
    # Place loop anchors along [20%, 80%] of the segment.
    usable_start = 0.20 * seg_len
    usable_end = 0.80 * seg_len
    usable_len = usable_end - usable_start

    # Pitch between loop entry points along the segment axis.
    if n_loops > 1:
        pitch = usable_len / (n_loops - 1)
    else:
        pitch = 0.0

    # Build new point list for this segment.
    pre_points: list[Point] = [p_start]
    # Walk to usable_start along the segment.
    pre_end = _pt(
        p_start.x + ux * usable_start,
        p_start.y + uy * usable_start,
    )
    pre_points.append(pre_end)

    current = pre_end
    for i in range(n_loops):
        t = usable_start + i * pitch
        anchor = _pt(
            p_start.x + ux * t,
            p_start.y + uy * t,
        )
        if i > 0:
            # Walk along segment to next anchor.
            current = anchor
            pre_points.append(current)

        # Loop corner on + side then - side (U-shape pointing in +perp direction).
        c1 = _pt(anchor.x + px * loop_height, anchor.y + py * loop_height)
        c2 = _pt(
            c1.x + ux * (trace_width + spacing), c1.y + uy * (trace_width + spacing)
        )
        c3 = _pt(c2.x - px * loop_height, c2.y - py * loop_height)
        pre_points.extend([c1, c2, c3])
        current = c3

    # Walk from last loop anchor to usable_end.
    post_start = _pt(
        p_start.x + ux * usable_end,
        p_start.y + uy * usable_end,
    )
    pre_points.append(post_start)
    pre_points.append(p_end)

    # Prefix with points before seg_idx, suffix with points after seg_idx+1.
    return tuple(points[:seg_idx]) + tuple(pre_points) + tuple(points[seg_idx + 2 :])


def _choose_best_segment(
    points: tuple[Point, ...],
) -> int:
    """Return the index of the longest segment (best candidate for loop insertion)."""
    best_idx = 0
    best_len = -1.0
    for i in range(len(points) - 1):
        sl = _seg_length(points[i], points[i + 1])
        if sl > best_len:
            best_len = sl
            best_idx = i
    return best_idx


def _apply_meander_single(
    route: RoutePolyline,
    target_length: float,
    min_loop_spacing: float,
) -> tuple[RoutePolyline, MeanderEdgeResult]:
    """Compute and insert meander loops for a single route."""
    actual_len = _polyline_length(route.points)
    excess = target_length - actual_len

    if excess <= _MIN_LOOP_SPACING_DEFAULT:
        result = MeanderEdgeResult(
            edge_id=route.edge_id,
            original_length_mm=actual_len,
            target_length_mm=target_length,
            achieved_length_mm=actual_len,
            n_loops=0,
            loop_height_mm=0.0,
            skipped=True,
            skip_reason="excess too small (< 0.15mm)",
        )
        return route, result

    trace_w = route.width
    spacing = max(min_loop_spacing, trace_w * 0.5)

    # Minimum loop height: must clear trace width + spacing on each side.
    h_min = trace_w * 2.0 + spacing
    # Each loop adds 2 * loop_height to the trace length.
    n_loops_min = max(1, math.ceil(excess / (2.0 * h_min * 5)))  # rough upper bound
    h_est = max(h_min, excess / (2.0 * max(n_loops_min, 1)))
    n_loops = max(1, math.ceil(excess / (2.0 * h_est)))
    # Exact loop height to achieve target length.
    h_exact = excess / (2.0 * n_loops)
    if h_exact < h_min:
        # Need more loops.
        n_loops = math.ceil(excess / (2.0 * h_min))
        h_exact = excess / (2.0 * n_loops)

    seg_idx = _choose_best_segment(route.points)
    seg_len = _seg_length(route.points[seg_idx], route.points[seg_idx + 1])

    # Each loop needs (trace_w + spacing) of longitudinal pitch; check segment fits.
    needed_pitch = n_loops * (trace_w + spacing)
    if seg_len < needed_pitch * 1.5:
        result = MeanderEdgeResult(
            edge_id=route.edge_id,
            original_length_mm=actual_len,
            target_length_mm=target_length,
            achieved_length_mm=actual_len,
            n_loops=0,
            loop_height_mm=0.0,
            skipped=True,
            skip_reason=(
                f"longest segment ({seg_len:.2f}mm) too short for "
                f"{n_loops} loops with pitch {trace_w + spacing:.2f}mm"
            ),
        )
        return route, result

    new_points = _insert_hairpin_loops(
        route.points, seg_idx, n_loops, h_exact, trace_w, spacing
    )
    achieved_len = _polyline_length(new_points)

    new_route = RoutePolyline(
        edge_id=route.edge_id,
        routing_class=route.routing_class,
        width=route.width,
        points=new_points,
    )
    result = MeanderEdgeResult(
        edge_id=route.edge_id,
        original_length_mm=actual_len,
        target_length_mm=target_length,
        achieved_length_mm=achieved_len,
        n_loops=n_loops,
        loop_height_mm=h_exact,
    )
    return new_route, result


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def apply_meanders(
    geom: GeometryIR,
    ir: SolverIR,
    *,
    min_loop_spacing: float = _MIN_LOOP_SPACING_DEFAULT,
) -> tuple[GeometryIR, MeanderReport]:
    """Apply U-shaped meander loops wherever target_length > actual routed length.

    Edges that do not appear in both *geom.routes* and *ir.edges*, or where the
    target length is already achieved (within 0.15mm), are skipped silently.

    Parameters
    ----------
    geom:
        Post-solve geometry (from M4 CP-SAT + optional M6 bend pass).
    ir:
        SolverIR containing per-edge ``target_length_mm`` values.
    min_loop_spacing:
        Minimum clearance between adjacent loop legs (mm).

    Returns
    -------
    (new_geom, report):
        ``new_geom`` is a copy of *geom* with meandered routes; ``report``
        records per-edge statistics.
    """
    acc_meandered: list[MeanderEdgeResult] = []
    acc_skipped: list[MeanderEdgeResult] = []
    acc_warnings: list[str] = []

    new_routes = dict(geom.routes)

    for edge_id, solver_edge in ir.edges.items():
        target = solver_edge.target_length
        if target is None:
            continue

        route = geom.routes.get(edge_id)
        if route is None:
            acc_warnings.append(
                f"edge {edge_id!r}: has target_length but no route in GeometryIR — skipped"
            )
            continue

        actual_len = _polyline_length(route.points)
        if actual_len >= target - _MIN_LOOP_SPACING_DEFAULT:
            # Already meets or exceeds target; skip quietly.
            continue

        new_route, edge_result = _apply_meander_single(route, target, min_loop_spacing)
        new_routes[edge_id] = new_route

        if edge_result.skipped:
            acc_skipped.append(edge_result)
            acc_warnings.append(
                f"edge {edge_id!r}: meander skipped — {edge_result.skip_reason}"
            )
        else:
            acc_meandered.append(edge_result)

    # Rebuild GeometryIR with updated routes.
    new_geom = GeometryIR(
        project=geom.project,
        board=geom.board,
        placements=geom.placements,
        routes=new_routes,
        nodes=geom.nodes,
        solve_status=geom.solve_status,
        solve_wall_seconds=geom.solve_wall_seconds,
        objective_value=geom.objective_value,
    )
    report = MeanderReport(
        meandered_edges=tuple(acc_meandered),
        skipped_edges=tuple(acc_skipped),
        warnings=tuple(acc_warnings),
    )
    return new_geom, report


__all__ = [
    "MeanderEdgeResult",
    "MeanderReport",
    "apply_meanders",
]
