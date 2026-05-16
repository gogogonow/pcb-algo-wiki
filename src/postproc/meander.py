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
    direction_sign: float = 1.0,
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
    px *= direction_sign
    py *= direction_sign

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


def _polyline_bbox_ok(
    points: tuple[Point, ...],
    board_min: tuple[float, float],
    board_max: tuple[float, float],
    half_w: float,
    on_axis_line: tuple[Point, Point] | None = None,
    on_axis_tol: float = 1e-3,
) -> bool:
    """Check that points (inflated by half_w) lie inside board bounds.

    If ``on_axis_line`` (start, end) is given, points within ``on_axis_tol``
    of that line are skipped — those are anchors on the original route axis
    whose validity is the caller's responsibility (the original endpoints
    may legitimately be on the board frame).
    """
    minx, miny = board_min
    maxx, maxy = board_max
    a = on_axis_line[0] if on_axis_line else None
    b = on_axis_line[1] if on_axis_line else None
    for p in points:
        if a is not None and b is not None:
            if _point_to_seg_dist(p, a, b) <= on_axis_tol:
                continue
        if (
            p.x - half_w < minx
            or p.x + half_w > maxx
            or p.y - half_w < miny
            or p.y + half_w > maxy
        ):
            return False
    return True


def _segments_cross(
    a1: Point, a2: Point, b1: Point, b2: Point, tol: float = 1e-6
) -> bool:
    """Proper crossing of two segments (shared endpoints not counted)."""
    if (
        (abs(a1.x - b1.x) < tol and abs(a1.y - b1.y) < tol)
        or (abs(a1.x - b2.x) < tol and abs(a1.y - b2.y) < tol)
        or (abs(a2.x - b1.x) < tol and abs(a2.y - b1.y) < tol)
        or (abs(a2.x - b2.x) < tol and abs(a2.y - b2.y) < tol)
    ):
        return False

    def _ccw(p: Point, q: Point, r: Point) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    d1 = _ccw(b1, b2, a1)
    d2 = _ccw(b1, b2, a2)
    d3 = _ccw(a1, a2, b1)
    d4 = _ccw(a1, a2, b2)
    return ((d1 > tol and d2 < -tol) or (d1 < -tol and d2 > tol)) and (
        (d3 > tol and d4 < -tol) or (d3 < -tol and d4 > tol)
    )


def _polyline_crosses_obstacles(
    new_points: tuple[Point, ...],
    obstacles: tuple[tuple[Point, ...], ...],
    inflate: float,
    on_axis_line: tuple[Point, Point] | None = None,
    on_axis_tol: float = 1e-3,
) -> bool:
    """Return True if any segment of new_points (inflated by ``inflate``)
    crosses any obstacle segment.

    Segments of ``new_points`` whose endpoints both lie on ``on_axis_line``
    are skipped — they coincide with the original route axis whose
    interaction with other routes is the caller's responsibility (and not
    something a meander can avoid).
    """
    a_axis = on_axis_line[0] if on_axis_line else None
    b_axis = on_axis_line[1] if on_axis_line else None
    for i in range(len(new_points) - 1):
        a1, a2 = new_points[i], new_points[i + 1]
        a1_on = (
            a_axis is not None
            and b_axis is not None
            and _point_to_seg_dist(a1, a_axis, b_axis) <= on_axis_tol
        )
        a2_on = (
            a_axis is not None
            and b_axis is not None
            and _point_to_seg_dist(a2, a_axis, b_axis) <= on_axis_tol
        )
        if a1_on and a2_on:
            continue
        for obs in obstacles:
            for j in range(len(obs) - 1):
                b1, b2 = obs[j], obs[j + 1]
                if _segments_cross(a1, a2, b1, b2):
                    return True
                # Proximity inflation — skip the endpoint that sits on the
                # original axis (its closeness to an obstacle is not the
                # meander's fault).
                if not a1_on and _point_to_seg_dist(a1, b1, b2) < inflate:
                    return True
                if not a2_on and _point_to_seg_dist(a2, b1, b2) < inflate:
                    return True
    return False


def _point_to_seg_dist(p: Point, a: Point, b: Point) -> float:
    """Shortest distance from p to segment ab."""
    dx, dy = b.x - a.x, b.y - a.y
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        return math.hypot(p.x - a.x, p.y - a.y)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / seg_len_sq))
    proj_x = a.x + t * dx
    proj_y = a.y + t * dy
    return math.hypot(p.x - proj_x, p.y - proj_y)


def _apply_meander_single(
    route: RoutePolyline,
    target_length: float,
    min_loop_spacing: float,
    board_bounds: tuple[float, float, float, float] | None = None,
    obstacles: tuple[tuple[Point, ...], ...] = (),
    obstacle_clearance: float = 0.0,
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

    # Constraint: hairpin must stay inside board frame AND must not cross any
    # other route. Try both perpendicular directions (sign = +1, -1) and, if
    # needed, halve the loop height (doubling n_loops) up to 3 attempts.
    half_w = trace_w / 2.0
    half_w_inflated = half_w + max(min_loop_spacing, 0.05)
    board_min = (board_bounds[0], board_bounds[1]) if board_bounds else None
    board_max = (board_bounds[2], board_bounds[3]) if board_bounds else None

    def _candidate_ok(pts: tuple[Point, ...]) -> bool:
        if board_min is not None and board_max is not None:
            if not _polyline_bbox_ok(
                pts,
                board_min,
                board_max,
                half_w_inflated,
                on_axis_line=(route.points[seg_idx], route.points[seg_idx + 1]),
            ):
                return False
        if obstacles and _polyline_crosses_obstacles(
            pts,
            obstacles,
            obstacle_clearance + half_w,
            on_axis_line=(route.points[seg_idx], route.points[seg_idx + 1]),
        ):
            return False
        return True

    accepted_points: tuple[Point, ...] | None = None
    final_n_loops = n_loops
    final_h = h_exact
    for attempt in range(3):
        attempt_loops = n_loops * (2**attempt)
        if attempt_loops <= 0:
            continue
        attempt_h = excess / (2.0 * attempt_loops)
        if attempt_h < h_min:
            continue
        attempt_pitch = attempt_loops * (trace_w + spacing)
        if seg_len < attempt_pitch * 1.5:
            continue
        for sign in (1.0, -1.0):
            candidate = _insert_hairpin_loops(
                route.points,
                seg_idx,
                attempt_loops,
                attempt_h,
                trace_w,
                spacing,
                direction_sign=sign,
            )
            if _candidate_ok(candidate):
                accepted_points = candidate
                final_n_loops = attempt_loops
                final_h = attempt_h
                break
        if accepted_points is not None:
            break

    if accepted_points is None:
        # Couldn't fit a legal hairpin — preserve original route, warn.
        result = MeanderEdgeResult(
            edge_id=route.edge_id,
            original_length_mm=actual_len,
            target_length_mm=target_length,
            achieved_length_mm=actual_len,
            n_loops=0,
            loop_height_mm=0.0,
            skipped=True,
            skip_reason=(
                "no legal hairpin direction (would exit board frame or "
                "cross another route)"
            ),
        )
        return route, result

    new_points = accepted_points
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
        n_loops=final_n_loops,
        loop_height_mm=final_h,
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

    # Board bounds for hairpin clamping.
    board = geom.board
    board_bounds = (
        float(board.origin.x),
        float(board.origin.y),
        float(board.origin.x) + float(board.width),
        float(board.origin.y) + float(board.height),
    )

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

        # Build obstacle list = every other current route.
        obstacles = tuple(
            new_routes[oid].points
            for oid in new_routes
            if oid != edge_id and len(new_routes[oid].points) >= 2
        )
        # Use the max width of the obstacle traces as conservative clearance.
        obstacle_clearance = (
            max(
                (new_routes[oid].width for oid in new_routes if oid != edge_id),
                default=0.0,
            )
            / 2.0
        )

        new_route, edge_result = _apply_meander_single(
            route,
            target,
            min_loop_spacing,
            board_bounds=board_bounds,
            obstacles=obstacles,
            obstacle_clearance=obstacle_clearance,
        )
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
