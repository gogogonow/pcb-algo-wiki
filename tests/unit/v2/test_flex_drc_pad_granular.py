"""端点豁免精确到 pad 级 — 防止 flex 路由穿同组件其他 pad."""

from __future__ import annotations

from schema.geometry_ir import RoutePolyline
from schema.v6_ir import Point, RoutingClass
from solver.v2.flex_drc import ObstacleEntry, validate_flex_routes


def _poly(*pts: tuple[float, float], eid: str = "e", w: float = 0.254) -> RoutePolyline:
    return RoutePolyline(
        edge_id=eid,
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=w,
        points=tuple(Point(x=x, y=y) for x, y in pts),
    )


def test_route_through_same_component_other_pad_reports_overlap():
    """路由从 U_BIAS.PIN_1 (1,5) 出发，到 C_DEC.PIN_1 (20,5)，
    途经 U_BIAS.PIN_2 (5,5)（同组件其他 pad）— 应报 overlap."""
    flex = {"e": _poly((1.0, 5.0), (10.0, 5.0), (20.0, 5.0), eid="e")}
    entries = [
        ObstacleEntry(bbox=(0.5, 4.5, 1.5, 5.5), kind="pad", owner_id="U_BIAS.PIN_1"),
        ObstacleEntry(bbox=(4.5, 4.5, 5.5, 5.5), kind="pad", owner_id="U_BIAS.PIN_2"),
        ObstacleEntry(
            bbox=(0.0, 4.0, 6.0, 6.0), kind="component_body", owner_id="U_BIAS"
        ),
        ObstacleEntry(bbox=(19.5, 4.5, 20.5, 5.5), kind="pad", owner_id="C_DEC.PIN_1"),
    ]
    rep = validate_flex_routes(
        flex_routes=flex,
        other_routes={},
        obstacle_entries=entries,
        endpoint_pin_ids={"e": ("U_BIAS.PIN_1", "C_DEC.PIN_1")},
        clearance_mm=0.05,
    )
    assert "e" in rep.failed_edges()
    assert any(v.kind == "overlap" for v in rep.violations)


def test_route_along_pin_exits_does_not_report():
    """同样的端点设定，但路径绕开 U_BIAS.PIN_2 — 不应报违规."""
    flex = {"e": _poly((1.0, 5.0), (1.0, 8.0), (20.0, 8.0), (20.0, 5.0), eid="e")}
    entries = [
        ObstacleEntry(bbox=(0.5, 4.5, 1.5, 5.5), kind="pad", owner_id="U_BIAS.PIN_1"),
        ObstacleEntry(bbox=(4.5, 4.5, 5.5, 5.5), kind="pad", owner_id="U_BIAS.PIN_2"),
        ObstacleEntry(
            bbox=(0.0, 4.0, 6.0, 6.0), kind="component_body", owner_id="U_BIAS"
        ),
        ObstacleEntry(bbox=(19.5, 4.5, 20.5, 5.5), kind="pad", owner_id="C_DEC.PIN_1"),
    ]
    rep = validate_flex_routes(
        flex_routes=flex,
        other_routes={},
        obstacle_entries=entries,
        endpoint_pin_ids={"e": ("U_BIAS.PIN_1", "C_DEC.PIN_1")},
        clearance_mm=0.05,
    )
    assert rep.violations == []


def test_endpoint_pad_self_is_exempted():
    """端点 pad 自身落在路径上 — 被豁免，不报."""
    flex = {"e": _poly((1.0, 5.0), (20.0, 5.0), eid="e")}
    entries = [
        ObstacleEntry(bbox=(0.5, 4.5, 1.5, 5.5), kind="pad", owner_id="U_BIAS.PIN_1"),
        ObstacleEntry(bbox=(19.5, 4.5, 20.5, 5.5), kind="pad", owner_id="C_DEC.PIN_1"),
    ]
    rep = validate_flex_routes(
        flex_routes=flex,
        other_routes={},
        obstacle_entries=entries,
        endpoint_pin_ids={"e": ("U_BIAS.PIN_1", "C_DEC.PIN_1")},
        clearance_mm=0.05,
    )
    assert rep.violations == []


def test_legacy_obstacle_bboxes_still_work():
    """向后兼容 — obstacle_bboxes 旧接口仍工作."""
    flex = {"a": _poly((0.0, 5.0), (10.0, 5.0), eid="a")}
    rep = validate_flex_routes(
        flex_routes=flex,
        other_routes={},
        obstacle_bboxes=[(4.0, 4.0, 6.0, 6.0)],
        clearance_mm=0.05,
    )
    assert "a" in rep.failed_edges()
