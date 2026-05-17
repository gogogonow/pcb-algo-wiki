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


def _sig(report) -> set[tuple[str, str, str]]:
    return {(v.edge_id, v.kind, v.detail) for v in report.violations}


def test_spatial_index_matches_naive_results() -> None:
    flex = {
        "a": _poly((0.0, 0.0), (10.0, 10.0), eid="a"),
        "b": _poly((0.0, 10.0), (10.0, 0.0), eid="b"),
        "c": _poly((0.0, 5.0), (10.0, 5.0), eid="c"),
        "d": _poly((1.0, 1.0), (1.0, 9.0), eid="d"),
    }
    other = {
        "m": RoutePolyline(
            edge_id="m",
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            width=0.5,
            points=(Point(x=6.0, y=0.0), Point(x=6.0, y=10.0)),
        )
    }
    obstacles = [
        (4.0, 4.0, 4.5, 6.0),
        (8.0, 8.0, 9.0, 9.0),
    ]
    naive = validate_flex_routes(
        flex_routes=flex,
        other_routes=other,
        obstacle_bboxes=obstacles,
        clearance_mm=0.05,
        use_spatial_index=False,
    )
    indexed = validate_flex_routes(
        flex_routes=flex,
        other_routes=other,
        obstacle_bboxes=obstacles,
        clearance_mm=0.05,
        use_spatial_index=True,
    )
    assert _sig(indexed) == _sig(naive)
