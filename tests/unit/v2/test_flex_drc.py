"""WI-J4 — phaseC flex_drc 单元测试."""

from __future__ import annotations

from schema.geometry_ir import RoutePolyline
from schema.v6_ir import Point, RoutingClass
from solver.v2.flex_drc import validate_flex_routes


def _poly(*pts: tuple[float, float], eid: str = "e", w: float = 0.254) -> RoutePolyline:
    return RoutePolyline(
        edge_id=eid,
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=w,
        points=tuple(Point(x=x, y=y) for x, y in pts),
    )


def test_disjoint_flex_routes_no_violation():
    flex = {
        "a": _poly((0.0, 0.0), (10.0, 0.0), eid="a"),
        "b": _poly((0.0, 5.0), (10.0, 5.0), eid="b"),
    }
    rep = validate_flex_routes(
        flex_routes=flex, other_routes={}, obstacle_bboxes=[], clearance_mm=0.05
    )
    assert rep.violations == []


def test_crossing_two_flex_routes_reports_both():
    flex = {
        "a": _poly((0.0, 0.0), (10.0, 10.0), eid="a"),
        "b": _poly((0.0, 10.0), (10.0, 0.0), eid="b"),
    }
    rep = validate_flex_routes(
        flex_routes=flex, other_routes={}, obstacle_bboxes=[], clearance_mm=0.05
    )
    failed = rep.failed_edges()
    assert "a" in failed and "b" in failed
    kinds = {v.kind for v in rep.violations}
    assert kinds == {"crossing"}


def test_flex_overlapping_obstacle_reports_overlap():
    flex = {"a": _poly((0.0, 5.0), (10.0, 5.0), eid="a")}
    rep = validate_flex_routes(
        flex_routes=flex,
        other_routes={},
        obstacle_bboxes=[(4.0, 4.0, 6.0, 6.0)],
        clearance_mm=0.05,
    )
    failed = rep.failed_edges()
    assert "a" in failed
    assert any(v.kind == "overlap" for v in rep.violations)


def test_flex_vs_other_route_crossing_reports_only_flex():
    """非 flex 走线被视为已存在的 microstrip，相交时只标 flex 失败。"""
    flex = {"f": _poly((0.0, 5.0), (10.0, 5.0), eid="f")}
    other = {
        "m": RoutePolyline(
            edge_id="m",
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            width=0.5,
            points=(Point(x=5.0, y=0.0), Point(x=5.0, y=10.0)),
        )
    }
    rep = validate_flex_routes(
        flex_routes=flex, other_routes=other, obstacle_bboxes=[], clearance_mm=0.05
    )
    assert rep.failed_edges() == {"f"}


def test_shared_endpoint_not_flagged_as_crossing():
    flex = {
        "a": _poly((0.0, 0.0), (5.0, 5.0), eid="a"),
        "b": _poly((5.0, 5.0), (10.0, 0.0), eid="b"),
    }
    rep = validate_flex_routes(
        flex_routes=flex, other_routes={}, obstacle_bboxes=[], clearance_mm=0.05
    )
    assert rep.violations == []
