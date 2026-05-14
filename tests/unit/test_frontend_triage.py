from frontend.triage import FLEX, RF_FREE, RF_LOCKED, UNKNOWN, triage_edge
from schema.v33 import Edge, EdgeConstraint, EdgeGeometry


def _edge(edge_type: str, target_length: float | None = None, **kwargs) -> Edge:
    constraint = (
        EdgeConstraint(width=0.5, target_length=target_length)
        if target_length is not None or "width" in kwargs
        else EdgeConstraint(width=0.5)
    )
    return Edge(
        type=edge_type,
        connections=["A", "B"],
        constraint=constraint,
        geometry=EdgeGeometry(bend_style="mitered_45"),
        **kwargs,
    )


def test_microstrip_with_target_length_locked() -> None:
    e = triage_edge("E1", _edge("microstrip", target_length=5.75))
    assert e.routing_class == RF_LOCKED
    assert e.target_length == 5.75


def test_microstrip_without_target_length_free() -> None:
    e = triage_edge("E2", _edge("microstrip"))
    assert e.routing_class == RF_FREE
    assert e.target_length is None


def test_trace_is_flexible() -> None:
    e = triage_edge("E3", _edge("trace"))
    assert e.routing_class == FLEX


def test_lumped_passes_through_routing_class() -> None:
    raw = Edge(type="lumped_series", connections=["A", "B"], routing_class="rf_lumped")
    e = triage_edge("E4", raw)
    assert e.routing_class == "rf_lumped"


def test_unknown_edge_type_returns_unknown() -> None:
    raw = Edge(type="ribbon", connections=["A", "B"])
    e = triage_edge("E5", raw)
    assert e.routing_class == UNKNOWN
