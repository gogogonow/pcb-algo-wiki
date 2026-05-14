"""Normalize node types (M2c part 2).

Aliases are unified per v6 §2.2:

* ``universal_node`` -> ``pad_junction``
* ``impedance_step`` -> ``stepped_impedance``

The following node types are preserved verbatim:
``universal_junction``, ``t_combiner_junction``, ``t_junction``,
``pad_junction``, ``stepped_impedance``, ``floating_shunt_tap``,
``component_pad_junction``.

Unknown node types are passed through (``normalized_type == original_type``)
so frontends remain forward-compatible.
"""

from __future__ import annotations

from typing import Any

from schema.v33 import Node, V33Layout

from .models import NormalizedNode

ALIAS_TABLE: dict[str, str] = {
    "universal_node": "pad_junction",
    "impedance_step": "stepped_impedance",
}

PRESERVED_TYPES: frozenset[str] = frozenset(
    {
        "universal_junction",
        "t_combiner_junction",
        "t_junction",
        "pad_junction",
        "stepped_impedance",
        "floating_shunt_tap",
        "component_pad_junction",
    }
)


def normalize_nodes(layout: V33Layout) -> dict[str, NormalizedNode]:
    return {name: normalize_node(name, node) for name, node in layout.nodes.items()}


def normalize_node(name: str, node: Node) -> NormalizedNode:
    original_type = node.type
    if original_type is None:
        normalized_type = "unknown"
    elif original_type in ALIAS_TABLE:
        normalized_type = ALIAS_TABLE[original_type]
    else:
        normalized_type = original_type

    branches: tuple[dict[str, Any], ...] = ()
    if node.connection_rules is not None and node.connection_rules.branches:
        branches = tuple(
            {
                "edge": branch.edge,
                "angle": branch.angle,
                "offset_u": (
                    branch.origin.offset_u if branch.origin is not None else None
                ),
                "offset_v": (
                    branch.origin.offset_v if branch.origin is not None else None
                ),
            }
            for branch in node.connection_rules.branches
        )

    return NormalizedNode(
        name=name,
        original_type=original_type,
        normalized_type=normalized_type,
        semantic_intent=tuple(node.semantic_intent),
        branches=branches,
    )


__all__ = ["ALIAS_TABLE", "PRESERVED_TYPES", "normalize_node", "normalize_nodes"]
