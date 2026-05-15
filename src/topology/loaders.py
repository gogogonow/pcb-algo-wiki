"""Load visualization-focused topology data from YAML."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from .model import (
    TopologyComponent,
    TopologyEdge,
    TopologyGraph,
    TopologyNode,
    TopologyPoint,
    TopologyTerminal,
)


def load_topology_graph(path: str | Path) -> TopologyGraph:
    """Load a minimal M0 topology graph from a YAML file."""

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return extract_topology_graph(data)


def extract_topology_graph(data: Mapping[str, object]) -> TopologyGraph:
    """Convert parsed YAML data into a visualization-focused topology graph."""

    if not isinstance(data, Mapping):
        raise ValueError("topology YAML root must be a mapping")

    graph = TopologyGraph()

    for component_id, component in _iter_section(data, "components", "component"):
        placement = _get_nested_mapping(component, component_id, "placement")
        pin_nets = _get_nested_mapping(component, component_id, "pin_nets")
        position = None
        if "x" in placement and "y" in placement:
            position = TopologyPoint(x=float(placement["x"]), y=float(placement["y"]))

        graph.components.append(
            TopologyComponent(
                id=component_id,
                placement_kind=placement.get("type", "fixed"),
                footprint_ref=component.get("footprint_ref"),
                label=component_id,
                position=position,
                pin_order=tuple(pin_nets.keys()),
                pin_ids=frozenset(pin_nets.keys()),
            )
        )

    for node_id, node in _iter_section(data, "nodes", "node"):
        graph.nodes.append(
            TopologyNode(
                id=node_id,
                kind=node.get("type", "node"),
                label=node_id,
            )
        )

    for terminal_id, terminal in _iter_section(data, "terminals", "terminal"):
        graph.terminals.append(
            TopologyTerminal(
                id=terminal_id,
                kind=terminal.get("type", "terminal"),
                label=terminal_id,
            )
        )

    for edge_id, edge in _iter_section(data, "edges", "edge"):
        connections = edge.get("connections", [])
        if len(connections) != 2:
            raise ValueError(f"edge {edge_id!r} must define exactly two connections")

        source = _validate_edge_endpoint(graph, edge_id, connections[0])
        target = _validate_edge_endpoint(graph, edge_id, connections[1])

        graph.edges.append(
            TopologyEdge(
                id=edge_id,
                source=source,
                target=target,
                kind=edge.get("type", "edge"),
                net=edge.get("net"),
            )
        )

    return graph


def _iter_section(
    data: Mapping[str, object],
    section_name: str,
    entity_name: str,
) -> list[tuple[str, Mapping[str, object]]]:
    section = data.get(section_name, {})
    if not isinstance(section, Mapping):
        raise ValueError(f"topology YAML section {section_name!r} must be a mapping")

    entities: list[tuple[str, Mapping[str, object]]] = []
    for entity_id, entity in section.items():
        if not isinstance(entity, Mapping):
            raise ValueError(f"{entity_name} {entity_id!r} must be a mapping")
        entities.append((entity_id, entity))

    return entities


def _validate_edge_endpoint(
    graph: TopologyGraph, edge_id: str, endpoint_id: object
) -> str:
    if not isinstance(endpoint_id, str):
        raise ValueError(
            f"edge {edge_id!r} references unresolved endpoint {endpoint_id!r}"
        )

    if "," in endpoint_id:
        members = [part.strip() for part in endpoint_id.split(",") if part.strip()]
        if not members:
            raise ValueError(
                f"edge {edge_id!r} references unresolved endpoint {endpoint_id!r}"
            )
        for member in members:
            _validate_edge_endpoint(graph, edge_id, member)
        if not any(node.id == endpoint_id for node in graph.nodes):
            graph.nodes.append(
                TopologyNode(
                    id=endpoint_id,
                    kind="composite_endpoint",
                    label=endpoint_id,
                )
            )
        return endpoint_id

    try:
        graph.resolve_endpoint(endpoint_id)
    except KeyError as exc:
        if "." in endpoint_id:
            raise ValueError(
                f"edge {edge_id!r} references unresolved endpoint {endpoint_id!r}"
            ) from exc
        if endpoint_id.endswith("_split_pad"):
            graph.nodes.append(
                TopologyNode(
                    id=endpoint_id,
                    kind="t_junction",
                    label=endpoint_id,
                )
            )
            return endpoint_id
        raise ValueError(
            f"edge {edge_id!r} references unresolved endpoint {endpoint_id!r}"
        ) from exc

    return endpoint_id


def _get_nested_mapping(
    entity: Mapping[str, object],
    component_id: str,
    field_name: str,
) -> Mapping[str, object]:
    value = entity.get(field_name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"component {component_id!r} {field_name} must be a mapping")
    return value
