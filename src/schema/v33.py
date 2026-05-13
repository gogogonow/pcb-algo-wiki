"""Lenient Pydantic models for rf_layout_simplified.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class LenientModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    @property
    def extra_fields(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Metadata(LenientModel):
    project_name: str | None = None
    version: str | None = None
    generator: str | None = None
    description: str | None = None
    timestamp: str | None = None


class Point(LenientModel):
    x: float | None = None
    y: float | None = None


class BoardOutline(LenientModel):
    type: str | None = None
    origin: Point | None = None
    width: float | None = None
    height: float | None = None


class RoutingConstraints(LenientModel):
    default_trace_width: float | None = None
    default_clearance: float | None = None
    via_drill: float | None = None
    via_pad: float | None = None


class Stackup(LenientModel):
    layer_count: int | None = None
    material: str | None = None
    board_thickness: float | None = None


class GlobalConstraints(LenientModel):
    routing: RoutingConstraints | None = None
    stackup: Stackup | None = None
    board_outline: BoardOutline | None = None
    keepout_zones: list[Any] = Field(default_factory=list)


class PadGeometry(LenientModel):
    shape: str | None = None
    width: float | None = None
    length: float | None = None


class FootprintPin(LenientModel):
    local_x: float | None = None
    local_y: float | None = None
    local_orientation: float | None = None
    pad_geometry: PadGeometry | None = None


class FootprintDimensions(LenientModel):
    width: float | None = None
    length: float | None = None
    height: float | None = None


class Footprint(LenientModel):
    description: str | None = None
    dimensions: FootprintDimensions = Field(default_factory=FootprintDimensions)
    pins: dict[str, FootprintPin] = Field(default_factory=dict)


class PlacementOrigin(LenientModel):
    offset_u: float | str | None = None
    offset_v: float | str | None = None


class ComponentPlacement(LenientModel):
    type: str | None = None
    x: float | None = None
    y: float | None = None
    rotation: float | None = None
    is_floating: bool | None = None
    anchor_pin: str | None = None
    reference_net: str | None = None
    origin: PlacementOrigin | None = None
    relative_rotation: float | None = None


class Component(LenientModel):
    footprint_ref: str | None = None
    placement: ComponentPlacement | None = None
    pin_nets: dict[str, str] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)


class NodeOrigin(LenientModel):
    offset_u: float | str | None = None
    offset_v: float | str | None = None


class NodeBranch(LenientModel):
    edge: str | None = None
    angle: float | None = None
    origin: NodeOrigin | None = None


class NodeConnectionRules(LenientModel):
    reference_edge: str | None = None
    branches: list[NodeBranch] = Field(default_factory=list)


class Node(LenientModel):
    type: str | None = None
    description: str | None = None
    semantic_intent: list[str] = Field(default_factory=list)
    connection_rules: NodeConnectionRules | None = None


class Terminal(LenientModel):
    type: str | None = None
    associated_component: str | None = None
    pin: str | None = None
    net: str | None = None


class EdgeConstraint(LenientModel):
    width: float | None = None
    target_length: float | None = None


class EdgeGeometry(LenientModel):
    bend_style: str | None = None
    launch_rule: str | None = None


class Edge(LenientModel):
    type: str | None = None
    net: str | None = None
    routing_class: str | None = None
    connections: list[str] = Field(default_factory=list)
    constraint: EdgeConstraint | None = None
    geometry: EdgeGeometry | None = None


class V33Layout(LenientModel):
    metadata: Metadata = Field(...)
    global_constraints: GlobalConstraints | None = Field(...)
    footprints: dict[str, Footprint] = Field(...)
    components: dict[str, Component] = Field(...)
    nodes: dict[str, Node] = Field(...)
    terminals: dict[str, Terminal] = Field(...)
    edges: dict[str, Edge] = Field(...)


def load_v33_layout(path: str | Path) -> V33Layout:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return V33Layout.model_validate(data)


__all__ = [
    "BoardOutline",
    "Component",
    "ComponentPlacement",
    "Edge",
    "EdgeConstraint",
    "EdgeGeometry",
    "Footprint",
    "FootprintDimensions",
    "FootprintPin",
    "GlobalConstraints",
    "LenientModel",
    "Metadata",
    "Node",
    "NodeBranch",
    "NodeConnectionRules",
    "NodeOrigin",
    "PadGeometry",
    "PlacementOrigin",
    "Point",
    "RoutingConstraints",
    "Stackup",
    "Terminal",
    "V33Layout",
    "load_v33_layout",
]
