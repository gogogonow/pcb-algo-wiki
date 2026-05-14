"""M10 Skeleton-First Router v2 (replacement for M3–M9 cpsat/sa/astar).

Three-phase pipeline:
* Phase A — skeleton routing: octilinear A* on a 100µm grid with priority-
  ordered routing (width × length descending) and rip-up & reroute.
* Phase B — UV adhesion: "dragged" UV components inherit the routed endpoint;
  free-floating UVs are scored against a free-space mask.
* Phase C — flexible_path / floating leftovers (delegates to ``astar_flex``;
  no-op for PA_Module_Simplified).

Public entry: :func:`solve_layout_v2` / :class:`OrchestratorV2Options`.
"""

from .channel_grid import ChannelGrid, GridConfig, RoutedPath, route_octilinear
from .node_planner import NodePlan, plan_node_positions
from .orchestrator import (
    OrchestratorV2Options,
    OrchestratorV2Result,
    PhaseAResult,
    PhaseBResult,
    PhaseCResult,
    solve_layout_v2,
)
from .skeleton_router import (
    EdgeRoutingPlan,
    RouteOutcome,
    SkeletonReport,
    route_skeleton,
)
from .uv_adhesion import UvAdhesionReport, adhere_uv_components

__all__ = [
    "ChannelGrid",
    "EdgeRoutingPlan",
    "GridConfig",
    "NodePlan",
    "OrchestratorV2Options",
    "OrchestratorV2Result",
    "PhaseAResult",
    "PhaseBResult",
    "PhaseCResult",
    "RouteOutcome",
    "RoutedPath",
    "SkeletonReport",
    "UvAdhesionReport",
    "adhere_uv_components",
    "plan_node_positions",
    "route_octilinear",
    "route_skeleton",
    "solve_layout_v2",
]
