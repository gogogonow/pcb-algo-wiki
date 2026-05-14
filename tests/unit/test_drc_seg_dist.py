"""M7 DRC segment-segment geometry tests.

Tests the upgraded clearance check that uses true segment-segment minimum
distance instead of inflated bbox overlap.
"""

from __future__ import annotations

import math

from postproc.drc import (
    _inflate_bbox,
    _polyline_polyline_min_dist,
    _seg_seg_min_dist,
    run_drc,
)
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=100.0, height=100.0)


def _pt(x: float, y: float) -> Point:
    return Point(x=float(x), y=float(y))


def _route(eid: str, width: float, pts: tuple[Point, ...]) -> RoutePolyline:
    return RoutePolyline(
        edge_id=eid,
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=width,
        points=pts,
    )


def _e(net: str | None = None, ep_a: str = "A", ep_b: str = "B") -> SolverEdge:
    return SolverEdge(
        endpoints=(ep_a, ep_b),
        routing_class=RoutingClass.FLEXIBLE_PATH,
        net=net,
    )


def _ir(edges: dict[str, SolverEdge], clearance: float = 0.15) -> SolverIR:
    return SolverIR(project="t", board=_board(), clearance=clearance, edges=edges)


def _geom(*routes: RoutePolyline) -> GeometryIR:
    return GeometryIR(
        project="t",
        board=_board(),
        routes={r.edge_id: r for r in routes},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
        objective_value=0.0,
    )


# ──────────────────────────────────────────────────────────────────────────────
# _seg_seg_min_dist tests
# ──────────────────────────────────────────────────────────────────────────────


def test_seg_seg_parallel_segments_horizontal() -> None:
    """Two parallel horizontal segments, 5mm apart."""
    p1, p2 = _pt(0, 0), _pt(10, 0)
    p3, p4 = _pt(0, 5), _pt(10, 5)
    assert abs(_seg_seg_min_dist(p1, p2, p3, p4) - 5.0) < 1e-6


def test_seg_seg_perpendicular_crossing() -> None:
    """Perpendicular segments that would cross if extended — but they cross."""
    p1, p2 = _pt(0, 5), _pt(10, 5)  # horizontal
    p3, p4 = _pt(5, 0), _pt(5, 10)  # vertical crossing at (5,5)
    assert abs(_seg_seg_min_dist(p1, p2, p3, p4)) < 1e-6


def test_seg_seg_no_crossing_endpoint() -> None:
    """Two segments that share an endpoint → distance = 0."""
    p1, p2 = _pt(0, 0), _pt(10, 0)
    p3, p4 = _pt(10, 0), _pt(10, 10)
    assert abs(_seg_seg_min_dist(p1, p2, p3, p4)) < 1e-6


def test_seg_seg_diagonal_no_touch() -> None:
    """Diagonal segments that DON'T touch — bbox might overlap but real distance > 0."""
    # Seg A: (0,0)→(10,10)   Seg B: (0,10)→(10,20)
    # These are parallel diagonals 10/√2 ≈ 7.07mm apart.
    p1, p2 = _pt(0, 0), _pt(10, 10)
    p3, p4 = _pt(0, 10), _pt(10, 20)
    d = _seg_seg_min_dist(p1, p2, p3, p4)
    expected = 10.0 / math.sqrt(2)  # perpendicular distance between parallel lines
    assert abs(d - expected) < 0.01


def test_seg_seg_point_to_seg() -> None:
    """Degenerate: seg1 is a zero-length 'point' at origin; seg2 is horizontal."""
    p1 = p2 = _pt(0, 3)  # point
    p3, p4 = _pt(-5, 0), _pt(5, 0)  # horizontal at y=0
    assert abs(_seg_seg_min_dist(p1, p2, p3, p4) - 3.0) < 1e-6


# ──────────────────────────────────────────────────────────────────────────────
# _polyline_polyline_min_dist tests
# ──────────────────────────────────────────────────────────────────────────────


def test_polyline_poly_distance() -> None:
    """Two L-shaped polylines 5mm apart."""
    poly_a = (_pt(0, 0), _pt(10, 0), _pt(10, 10))
    poly_b = (_pt(0, 5), _pt(5, 5), _pt(5, 15))
    d = _polyline_polyline_min_dist(poly_a, poly_b)
    assert d < 5.1  # they are 5mm apart horizontally at the nearest segments


# ──────────────────────────────────────────────────────────────────────────────
# run_drc with segment-segment distance
# ──────────────────────────────────────────────────────────────────────────────


def test_drc_diagonal_segments_no_false_positive() -> None:
    """Diagonal parallel traces 3mm apart, 0.5mm wide each — clearance=0.15mm.

    Required gap = 0.25 + 0.25 + 0.15 = 0.65mm.  Actual = 3mm/√2 ≈ 2.12mm.
    Old bbox check would have flagged this; new seg-dist check should pass.
    """
    r_a = _route("EA", 0.5, (_pt(0, 0), _pt(10, 10)))
    r_b = _route("EB", 0.5, (_pt(0, 3), _pt(10, 13)))
    g = _geom(r_a, r_b)
    ir = _ir(
        {"EA": _e("NET_A", "A1", "A2"), "EB": _e("NET_B", "B1", "B2")}, clearance=0.15
    )
    report = run_drc(g, ir)
    assert (
        report.critical_count == 0
    ), f"Unexpected DRC criticals: {[v.detail for v in report.violations if v.severity == 'critical']}"


def test_drc_close_traces_flagged_as_critical() -> None:
    """Two traces with only 0.05mm gap (clearance=0.15mm) → critical violation."""
    # Route A: y=0, width=0.5 → top edge at y=0.25
    # Route B: y=0.35, width=0.5 → bottom edge at y=0.35-0.25=0.10
    # Edge-to-edge gap = 0.35 - 0.25 - 0.25 = -0.15mm (overlap!) → definitely critical.
    r_a = _route("EA", 0.5, (_pt(0, 0.0), _pt(20, 0.0)))
    r_b = _route("EB", 0.5, (_pt(0, 0.35), _pt(20, 0.35)))
    g = _geom(r_a, r_b)
    ir = _ir(
        {"EA": _e("NET_A", "A1", "A2"), "EB": _e("NET_B", "B1", "B2")}, clearance=0.15
    )
    report = run_drc(g, ir)
    assert report.critical_count >= 1


def test_drc_same_net_skipped() -> None:
    """Same-net traces are never checked for clearance violations."""
    r_a = _route("EA", 0.5, (_pt(0, 0), _pt(10, 0)))
    r_b = _route("EB", 0.5, (_pt(0, 0.1), _pt(10, 0.1)))  # very close, same net
    g = _geom(r_a, r_b)
    ir = _ir(
        {"EA": _e("SAME", "A1", "A2"), "EB": _e("SAME", "B1", "B2")}, clearance=0.15
    )
    report = run_drc(g, ir)
    clearance_violations = [v for v in report.violations if v.rule == "min_clearance"]
    assert len(clearance_violations) == 0


def test_drc_touching_endpoint_skipped() -> None:
    """Traces sharing an endpoint (adjacent segments) are excluded from clearance check."""
    r_a = _route("EA", 0.5, (_pt(0, 0), _pt(10, 0)))
    r_b = _route("EB", 0.5, (_pt(10, 0), _pt(10, 10)))
    g = _geom(r_a, r_b)
    # Make them share an endpoint by putting same string in endpoints set.
    ea = SolverEdge(
        endpoints=("pad_shared", "pad_A"),
        routing_class=RoutingClass.FLEXIBLE_PATH,
        net="X",
    )
    eb = SolverEdge(
        endpoints=("pad_shared", "pad_B"),
        routing_class=RoutingClass.FLEXIBLE_PATH,
        net="Y",
    )
    ir = _ir({"EA": ea, "EB": eb}, clearance=0.15)
    report = run_drc(g, ir)
    clearance_violations = [v for v in report.violations if v.rule == "min_clearance"]
    assert len(clearance_violations) == 0


def test_legacy_inflate_bbox_still_works() -> None:
    """_inflate_bbox is still importable and returns 4-tuple for backward compat."""
    pts = (_pt(0, 0), _pt(10, 0))
    result = _inflate_bbox(pts, width=0.5, clearance=0.15)
    assert len(result) == 4
    # BBox should be expanded by half_width + clearance = 0.25 + 0.15 = 0.40mm
    # origin: min_x = -0.40, max_x = 10.40, min_y = -0.40, max_y = 0.40
    assert result[0] < 0  # min_x < 0
    assert result[2] > 10000  # max_x > 10mm in µm scale
