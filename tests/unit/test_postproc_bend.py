"""M6 unit tests for ``postproc.bend.apply_bends``."""

from __future__ import annotations

import math

from postproc.bend import apply_bends
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=50.0, height=50.0)


def _l_route(edge_id: str) -> RoutePolyline:
    return RoutePolyline(
        edge_id=edge_id,
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=0.3,
        points=(Point(x=0.0, y=0.0), Point(x=10.0, y=0.0), Point(x=10.0, y=10.0)),
    )


def _build(
    edges: dict[str, SolverEdge], routes: dict[str, RoutePolyline]
) -> tuple[GeometryIR, SolverIR]:
    geom = GeometryIR(
        project="t",
        board=_board(),
        placements={},
        routes=routes,
        nodes={},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
    )
    ir = SolverIR(project="t", board=_board(), clearance=0.15, edges=edges)
    return geom, ir


def _edge(bend_style: str | None) -> SolverEdge:
    return SolverEdge(
        endpoints=("a", "b"),
        routing_class=RoutingClass.FLEXIBLE_PATH,
        bend_style=bend_style,
    )


def test_square_or_unset_keeps_points() -> None:
    geom, ir = _build({"e1": _edge(None)}, {"e1": _l_route("e1")})
    bended, report = apply_bends(geom, ir)
    assert bended.routes["e1"].points == geom.routes["e1"].points
    assert "e1" in report.skipped_edges
    assert report.warnings == ()


def test_mitered_45_inserts_two_points_per_corner() -> None:
    geom, ir = _build({"e1": _edge("mitered_45")}, {"e1": _l_route("e1")})
    bended, report = apply_bends(geom, ir)
    pts = bended.routes["e1"].points
    # original 3 points: start, corner, end → mitered: start, p_in, p_out, end
    assert len(pts) == 4
    assert "e1" in report.bended_edges
    # chamfer must lie on the original arms
    p_in, p_out = pts[1], pts[2]
    assert math.isclose(float(p_in.y), 0.0, abs_tol=1e-9)
    assert math.isclose(float(p_out.x), 10.0, abs_tol=1e-9)


def test_rounded_arc_adds_multiple_points() -> None:
    geom, ir = _build({"e1": _edge("rounded")}, {"e1": _l_route("e1")})
    bended, _ = apply_bends(geom, ir)
    pts = bended.routes["e1"].points
    # 2 endpoints + arc approximation (>=4 points)
    assert len(pts) >= 6
    # all arc inner points lie within 1mm of corner (10, 0)
    for p in pts[1:-1]:
        d = math.hypot(float(p.x) - 10.0, float(p.y) - 0.0)
        assert d <= 2.0  # radius bounded by min(seg/3, 2*width)


def test_curved_alias_of_rounded() -> None:
    geom_r, ir_r = _build({"e1": _edge("rounded")}, {"e1": _l_route("e1")})
    geom_c, ir_c = _build({"e1": _edge("curved")}, {"e1": _l_route("e1")})
    pts_r = apply_bends(geom_r, ir_r)[0].routes["e1"].points
    pts_c = apply_bends(geom_c, ir_c)[0].routes["e1"].points
    assert pts_r == pts_c


def test_unknown_style_falls_back_with_warning() -> None:
    geom, ir = _build({"e1": _edge("zigzag")}, {"e1": _l_route("e1")})
    bended, report = apply_bends(geom, ir)
    assert bended.routes["e1"].points == geom.routes["e1"].points
    assert any("zigzag" in w for w in report.warnings)
    assert "e1" in report.skipped_edges


def test_collinear_points_are_not_treated_as_corners() -> None:
    route = RoutePolyline(
        edge_id="e1",
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=0.3,
        points=(
            Point(x=0.0, y=0.0),
            Point(x=5.0, y=0.0),
            Point(x=10.0, y=0.0),
        ),
    )
    geom, ir = _build({"e1": _edge("mitered_45")}, {"e1": route})
    bended, report = apply_bends(geom, ir)
    assert bended.routes["e1"].points == route.points
    assert "e1" in report.skipped_edges


def test_two_point_route_is_passthrough() -> None:
    route = RoutePolyline(
        edge_id="e1",
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=0.3,
        points=(Point(x=0.0, y=0.0), Point(x=10.0, y=0.0)),
    )
    geom, ir = _build({"e1": _edge("mitered_45")}, {"e1": route})
    bended, _ = apply_bends(geom, ir)
    assert bended.routes["e1"].points == route.points
