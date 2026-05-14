"""Routing-class triage (M2c part 1).

Implements decision **D3**:

* ``microstrip`` with ``constraint.target_length`` -> ``rf_constrained_locked``
* ``microstrip`` without ``target_length``        -> ``rf_constrained_free``
* ``trace``                                       -> ``flexible_path``
* anything else (including ``lumped_*`` v4 fallback) is passed through with
  the original ``routing_class`` if present, otherwise ``unknown``.
"""

from __future__ import annotations

from schema.v33 import Edge, V33Layout

from .models import TriagedEdge

RF_LOCKED = "rf_constrained_locked"
RF_FREE = "rf_constrained_free"
FLEX = "flexible_path"
UNKNOWN = "unknown"


def triage_edges(layout: V33Layout) -> dict[str, TriagedEdge]:
    return {name: triage_edge(name, edge) for name, edge in layout.edges.items()}


def triage_edge(name: str, edge: Edge) -> TriagedEdge:
    target_length = (
        edge.constraint.target_length if edge.constraint is not None else None
    )
    width = edge.constraint.width if edge.constraint is not None else None

    edge_type = (edge.type or "").strip()
    if edge_type == "microstrip":
        routing_class = RF_LOCKED if target_length is not None else RF_FREE
    elif edge_type == "trace":
        routing_class = FLEX
    elif edge_type.startswith("lumped"):
        routing_class = edge.routing_class or UNKNOWN
    else:
        routing_class = edge.routing_class or UNKNOWN

    return TriagedEdge(
        name=name,
        edge_type=edge_type or UNKNOWN,
        routing_class=routing_class,
        target_length=target_length,
        width=width,
        connections=tuple(edge.connections),
        bend_style=(edge.geometry.bend_style if edge.geometry else None),
        launch_rule=(edge.geometry.launch_rule if edge.geometry else None),
    )


__all__ = ["FLEX", "RF_FREE", "RF_LOCKED", "UNKNOWN", "triage_edge", "triage_edges"]
