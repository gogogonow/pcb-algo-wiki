"""Minimal topology data model for M0 extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar


@dataclass(frozen=True, slots=True)
class TopologyPoint:
    """2D point in the topology canvas."""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class TopologyNode:
    """Logical YAML node for topology visualization."""

    id: str
    kind: str
    label: str | None = None
    position: TopologyPoint | None = None


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    """Connection between two topology entities."""

    id: str
    source: str
    target: str
    kind: str = "edge"
    net: str | None = None


@dataclass(frozen=True, slots=True)
class TopologyComponent:
    """Component vertex in the extracted topology graph."""

    id: str
    placement_kind: str
    footprint_ref: str | None = None
    label: str | None = None
    position: TopologyPoint | None = None
    pin_order: tuple[str, ...] = ()
    pin_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class TopologyTerminal:
    """External terminal vertex in the extracted topology graph."""

    id: str
    kind: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class TopologyEndpointRef:
    """Resolved edge endpoint for graph consumers."""

    id: str
    entity: TopologyComponent | TopologyNode | TopologyTerminal
    entity_kind: str
    pin_id: str | None = None

    @property
    def entity_id(self) -> str:
        return self.entity.id


T = TypeVar("T")


@dataclass(slots=True)
class TopologyGraph:
    """Container for visualization-focused topology entities."""

    components: list[TopologyComponent] = field(default_factory=list)
    nodes: list[TopologyNode] = field(default_factory=list)
    terminals: list[TopologyTerminal] = field(default_factory=list)
    edges: list[TopologyEdge] = field(default_factory=list)

    @property
    def fixed_components(self) -> list[TopologyComponent]:
        return [component for component in self.components if component.placement_kind == "fixed"]

    @property
    def parametric_uv_components(self) -> list[TopologyComponent]:
        return [
            component
            for component in self.components
            if component.placement_kind == "parametric_uv"
        ]

    def get_component(self, component_id: str) -> TopologyComponent:
        return self._get_by_id(self.components, component_id)

    def get_node(self, node_id: str) -> TopologyNode:
        return self._get_by_id(self.nodes, node_id)

    def get_terminal(self, terminal_id: str) -> TopologyTerminal:
        return self._get_by_id(self.terminals, terminal_id)

    def get_edge(self, edge_id: str) -> TopologyEdge:
        return self._get_by_id(self.edges, edge_id)

    def resolve_endpoint(self, endpoint_id: str) -> TopologyEndpointRef:
        node = self._find_by_id(self.nodes, endpoint_id)
        if node is not None:
            return TopologyEndpointRef(id=endpoint_id, entity=node, entity_kind="node")

        terminal = self._find_by_id(self.terminals, endpoint_id)
        if terminal is not None:
            return TopologyEndpointRef(id=endpoint_id, entity=terminal, entity_kind="terminal")

        component = self._find_by_id(self.components, endpoint_id)
        if component is not None:
            return TopologyEndpointRef(id=endpoint_id, entity=component, entity_kind="component")

        component_id, separator, pin_id = endpoint_id.partition(".")
        if separator:
            component = self._find_by_id(self.components, component_id)
            if component is not None and pin_id in component.pin_ids:
                return TopologyEndpointRef(
                    id=endpoint_id,
                    entity=component,
                    entity_kind="component",
                    pin_id=pin_id,
                )

        raise KeyError(endpoint_id)

    @staticmethod
    def _get_by_id(items: list[T], item_id: str) -> T:
        item = TopologyGraph._find_by_id(items, item_id)
        if item is not None:
            return item
        raise KeyError(item_id)

    @staticmethod
    def _find_by_id(items: list[T], item_id: str) -> T | None:
        for item in items:
            if item.id == item_id:
                return item
        return None
