"""M2 Frontend Compiler package."""

from .compile import artifact_to_dict, compile_layout
from .expand_components import expand_component, expand_components
from .lint import lint_layout
from .models import (
    BBox,
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintError,
    LintRepair,
    LintReport,
    LintWarning,
    NormalizedNode,
    Obstacle,
    TriagedEdge,
    UvMeta,
)
from .normalize_nodes import normalize_node, normalize_nodes
from .obstacles import build_obstacles
from .triage import triage_edge, triage_edges

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
    "TriagedEdge",
    "UvMeta",
    "artifact_to_dict",
    "build_obstacles",
    "compile_layout",
    "expand_component",
    "expand_components",
    "lint_layout",
    "normalize_node",
    "normalize_nodes",
    "triage_edge",
    "triage_edges",
]
