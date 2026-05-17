from __future__ import annotations

from solver.v2.skeleton_router import EdgeRoutingPlan, _build_endpoint_to_route_ids


def test_build_endpoint_to_route_ids_maps_both_endpoints() -> None:
    routes_plan = [
        EdgeRoutingPlan(
            edge_id="e1",
            start_endpoint="A.P1",
            goal_endpoint="N1",
            width_mm=1.0,
            target_length_mm=None,
            priority=1.0,
        ),
        EdgeRoutingPlan(
            edge_id="e2",
            start_endpoint="B.P1",
            goal_endpoint="N1",
            width_mm=1.0,
            target_length_mm=None,
            priority=1.0,
        ),
    ]
    index = _build_endpoint_to_route_ids(routes_plan)
    assert index["N1"] == {"e1", "e2"}
    assert index["A.P1"] == {"e1"}
    assert index["B.P1"] == {"e2"}
