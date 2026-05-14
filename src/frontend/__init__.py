"""M2/M3 Frontend Compiler package."""

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
from .solver_ir import compile_solver_ir, host_match_summary, solver_ir_to_dict
from .triage import triage_edge, triage_edges
from .universal_junction import expand_universal_junctions
from .uv_resolver import host_match_histogram, resolve_uv_components

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
    "compile_solver_ir",
    "expand_component",
    "expand_components",
    "expand_universal_junctions",
    "host_match_histogram",
    "host_match_summary",
    "lint_layout",
    "normalize_node",
    "normalize_nodes",
    "resolve_uv_components",
    "solver_ir_to_dict",
    "triage_edge",
    "triage_edges",
]
