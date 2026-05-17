"""Intermediate dataclasses produced by the M2 Frontend Compiler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PadKind = Literal["fixed", "uv_deferred", "floating_deferred"]
ObstacleKind = Literal["board_outline", "keepout", "footprint_bbox"]
PlacementKind = Literal["fixed", "parametric_uv", "floating", "unknown"]


@dataclass(frozen=True)
class LintRepair:
    field_path: str
    before: str
    after: str
    kind: str  # e.g. "typo"


@dataclass(frozen=True)
class LintWarning:
    field_path: str
    code: str
    message: str


@dataclass(frozen=True)
class LintError:
    field_path: str
    code: str
    message: str


@dataclass(frozen=True)
class LintReport:
    repairs: tuple[LintRepair, ...] = ()
    warnings: tuple[LintWarning, ...] = ()
    errors: tuple[LintError, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ExpandedPad:
    component: str
    pin: str
    abs_x: float | None
    abs_y: float | None
    orientation: float | None
    kind: PadKind
    local_x: float = 0.0
    local_y: float = 0.0
    pad_width: float | None = None
    pad_length: float | None = None


@dataclass(frozen=True)
class BBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def as_polygon(self) -> tuple[tuple[float, float], ...]:
        return (
            (self.min_x, self.min_y),
            (self.max_x, self.min_y),
            (self.max_x, self.max_y),
            (self.min_x, self.max_y),
        )


@dataclass(frozen=True)
class UvMeta:
    anchor_pin: str
    reference_net: str | None
    rotation_domain: tuple[int, ...] = (0, 90, 180, 270)


@dataclass(frozen=True)
class ComponentExpansion:
    name: str
    footprint_ref: str | None
    placement_kind: PlacementKind
    pads: tuple[ExpandedPad, ...]
    bbox: BBox | None = None
    uv_meta: UvMeta | None = None
    pin_nets: dict[str, str] = field(default_factory=dict)
    footprint_dims_mm: tuple[float, float] | None = (
        None  # (width, length) in component-local frame
    )


@dataclass(frozen=True)
class Obstacle:
    kind: ObstacleKind
    label: str
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class TriagedEdge:
    name: str
    edge_type: str
    routing_class: str
    target_length: float | None
    width: float | None
    connections: tuple[str, ...]
    bend_style: str | None = None
    launch_rule: str | None = None
    net: str | None = None


@dataclass(frozen=True)
class NormalizedNode:
    name: str
    original_type: str | None
    normalized_type: str
    semantic_intent: tuple[str, ...] = ()
    branches: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class FrontendArtifact:
    project_name: str | None
    board: dict[str, Any]
    lint_report: LintReport
    components: dict[str, ComponentExpansion] = field(default_factory=dict)
    fixed_terminals: dict[str, ExpandedPad] = field(default_factory=dict)
    uv_components: dict[str, ComponentExpansion] = field(default_factory=dict)
    obstacles: tuple[Obstacle, ...] = ()
    edges: dict[str, TriagedEdge] = field(default_factory=dict)
    nodes: dict[str, NormalizedNode] = field(default_factory=dict)

    def edges_by_class(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.edges.values():
            out[e.routing_class] = out.get(e.routing_class, 0) + 1
        return out

    def nodes_by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes.values():
            out[n.normalized_type] = out.get(n.normalized_type, 0) + 1
        return out


__all__ = [
    "BBox",
    "ComponentExpansion",
    "ExpandedPad",
    "FrontendArtifact",
    "LintError",
    "LintRepair",
    "LintReport",
    "LintWarning",
    "NormalizedNode",
    "Obstacle",
    "ObstacleKind",
    "PadKind",
    "PlacementKind",
    "TriagedEdge",
    "UvMeta",
]
