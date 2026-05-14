from frontend.normalize_nodes import ALIAS_TABLE, PRESERVED_TYPES, normalize_node
from schema.v33 import (
    Node,
    NodeBranch,
    NodeConnectionRules,
    NodeOrigin,
)


def _node(node_type: str | None, *, branches=None) -> Node:
    rules = (
        NodeConnectionRules(
            branches=[
                NodeBranch(
                    edge=b["edge"],
                    angle=b["angle"],
                    origin=NodeOrigin(offset_u=b["offset_u"], offset_v=b["offset_v"]),
                )
                for b in branches
            ]
        )
        if branches
        else None
    )
    return Node(type=node_type, connection_rules=rules, semantic_intent=["x"])


def test_alias_universal_node() -> None:
    n = normalize_node("N1", _node("universal_node"))
    assert n.normalized_type == ALIAS_TABLE["universal_node"]
    assert n.original_type == "universal_node"


def test_preserved_universal_junction() -> None:
    n = normalize_node(
        "N2",
        _node(
            "universal_junction",
            branches=[
                {"edge": "E", "angle": 90.0, "offset_u": -3.94, "offset_v": "edge_left"}
            ],
        ),
    )
    assert n.normalized_type == "universal_junction"
    assert "universal_junction" in PRESERVED_TYPES
    assert n.branches[0]["angle"] == 90.0
    assert n.branches[0]["offset_v"] == "edge_left"


def test_t_combiner_preserved() -> None:
    n = normalize_node("N3", _node("t_combiner_junction"))
    assert n.normalized_type == "t_combiner_junction"


def test_unknown_node_passes_through() -> None:
    n = normalize_node("N4", _node("exotic_type"))
    assert n.normalized_type == "exotic_type"


def test_missing_type_becomes_unknown() -> None:
    n = normalize_node("N5", _node(None))
    assert n.normalized_type == "unknown"
