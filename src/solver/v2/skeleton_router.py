"""Phase A — skeleton routing entry point.

Routes every ``rf_constrained_*`` edge with octilinear A*, in priority
order ``width × max(target_length, manhattan_distance)`` descending, with a
single-pass rip-up & reroute loop (see :mod:`rip_up`) to recover from edges
that block later siblings.

Outputs an :class:`SkeletonReport` carrying per-edge polylines (µm) plus
final endpoint coordinates (so Phase B can read where each "dragged" UV
ended up).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from frontend.models import FrontendArtifact, TriagedEdge
from solver.units import MM_TO_UM

from .channel_grid import ChannelGrid, GridConfig, route_octilinear
from .node_planner import NodePlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EdgeRoutingPlan:
    edge_id: str
    start_endpoint: str
    goal_endpoint: str
    width_mm: float
    target_length_mm: float | None
    priority: float


@dataclass(frozen=True)
class RouteOutcome:
    edge_id: str
    polyline_um: tuple[tuple[int, int], ...]
    length_mm: float
    target_mm: float | None
    success: bool
    rip_up_round: int
    failure_reason: str | None = None

    @property
    def length_err_pct(self) -> float | None:
        if self.target_mm is None or self.target_mm <= 0:
            return None
        return (self.length_mm - self.target_mm) / self.target_mm * 100.0


@dataclass
class SkeletonReport:
    routes: dict[str, RouteOutcome] = field(default_factory=dict)
    final_endpoint_um: dict[str, tuple[int, int]] = field(default_factory=dict)
    rip_up_rounds: int = 0

    def success_rate(self) -> tuple[int, int]:
        ok = sum(1 for r in self.routes.values() if r.success)
        return ok, len(self.routes)


# Priority weights for the 3-bucket triage from the plan.
_WIDTH_GROUP_BONUS = {
    "wide": 100.0,
    "mid": 60.0,
    "thin": 30.0,
}


def _bucket_for_width(width: float | None) -> str:
    if width is None:
        return "thin"
    if width >= 1.5:
        return "wide"
    if width >= 1.0:
        return "mid"
    return "thin"


def _build_routing_plan(
    edges: Iterable[TriagedEdge], plan: NodePlan
) -> list[EdgeRoutingPlan]:
    out: list[EdgeRoutingPlan] = []
    for edge in edges:
        if not edge.routing_class.startswith("rf_constrained"):
            continue
        if len(edge.connections) != 2:
            continue
        a, b = edge.connections
        ax, ay = plan.endpoint_xy.get(a, (0.0, 0.0))
        bx, by = plan.endpoint_xy.get(b, (0.0, 0.0))
        manhattan = abs(ax - bx) + abs(ay - by)
        target = edge.target_length if edge.target_length else manhattan
        bucket_bonus = _WIDTH_GROUP_BONUS[_bucket_for_width(edge.width)]
        priority = bucket_bonus + (edge.width or 0.5) * max(target, 1.0)
        out.append(
            EdgeRoutingPlan(
                edge_id=edge.name,
                start_endpoint=a,
                goal_endpoint=b,
                width_mm=edge.width or 0.5,
                target_length_mm=edge.target_length,
                priority=priority,
            )
        )
    out.sort(key=lambda p: -p.priority)
    return out


def _make_grid(
    artifact: FrontendArtifact,
    *,
    clearance_mm: float,
    config: GridConfig,
) -> ChannelGrid:
    board = artifact.board
    width = float(board.get("width", 40.0))
    height = float(board.get("height", 100.0))
    grid = ChannelGrid(
        board_min=(0, 0),
        board_max=(int(width * MM_TO_UM), int(height * MM_TO_UM)),
        config=config,
    )
    clearance_um = max(int(clearance_mm * MM_TO_UM), config.step_um // 2)
    # Add fixed footprints (IC1, TP1-5) as inflated AABBs.
    for comp_name, comp in artifact.components.items():
        if comp.placement_kind != "fixed" or comp.bbox is None:
            continue
        bbox = comp.bbox
        # Don't inflate over the pad footprint itself, but add a small halo
        # equal to clearance/2 so routes still land on the pad.
        halo = clearance_um // 2
        grid.add_fixed_aabb(
            int(bbox.min_x * MM_TO_UM) - halo,
            int(bbox.min_y * MM_TO_UM) - halo,
            int(bbox.max_x * MM_TO_UM) + halo,
            int(bbox.max_y * MM_TO_UM) + halo,
            f"footprint:{comp_name}",
        )
    return grid


def _carve_pad_corridor(
    grid: ChannelGrid,
    artifact: FrontendArtifact,
    plan: NodePlan,
) -> dict[str, str]:
    """Return ``endpoint -> ignore_label`` so A* may pass through that footprint.

    Without this, IC1's pad stops are inside the inflated bbox of IC1 itself
    and A* refuses to approach. We tag each fixed pad's "owner footprint" so
    the route from / to that pad temporarily ignores its own footprint halo.
    """
    out: dict[str, str] = {}
    for endpoint, _xy in plan.endpoint_xy.items():
        if "." not in endpoint:
            continue
        comp_name = endpoint.split(".", 1)[0]
        out[endpoint] = f"footprint:{comp_name}"
    return out


def route_skeleton(
    artifact: FrontendArtifact,
    plan: NodePlan,
    *,
    clearance_mm: float = 0.15,
    grid_config: GridConfig | None = None,
    rip_up_rounds: int = 5,
) -> SkeletonReport:
    """Route every RF edge using a priority queue with simple rip-up."""

    config = grid_config or GridConfig()
    grid = _make_grid(artifact, clearance_mm=clearance_mm, config=config)
    pad_owners = _carve_pad_corridor(grid, artifact, plan)
    routes_plan = _build_routing_plan(artifact.edges.values(), plan)

    report = SkeletonReport()
    pending = list(routes_plan)
    seen_ripup = 0
    failed_edges: dict[str, int] = {}  # edge_id -> attempts
    clearance_um = int(clearance_mm * MM_TO_UM)

    for round_idx in range(rip_up_rounds + 1):
        report.rip_up_rounds = round_idx
        next_pending: list[EdgeRoutingPlan] = []
        for ep in pending:
            start = plan.endpoint_xy.get(ep.start_endpoint)
            goal = plan.endpoint_xy.get(ep.goal_endpoint)
            if start is None or goal is None:
                report.routes[ep.edge_id] = RouteOutcome(
                    edge_id=ep.edge_id,
                    polyline_um=(),
                    length_mm=0.0,
                    target_mm=ep.target_length_mm,
                    success=False,
                    rip_up_round=round_idx,
                    failure_reason="endpoint not resolved",
                )
                continue
            ignore = []
            for ep_name in (ep.start_endpoint, ep.goal_endpoint):
                tag = pad_owners.get(ep_name)
                if tag:
                    ignore.append(tag)
            ignore_tuple = tuple(ignore)
            start_um = (int(start[0] * MM_TO_UM), int(start[1] * MM_TO_UM))
            goal_um = (int(goal[0] * MM_TO_UM), int(goal[1] * MM_TO_UM))
            max_len_um: int | None = None
            if ep.target_length_mm is not None:
                max_len_um = int(ep.target_length_mm * MM_TO_UM * 1.5)
            path = route_octilinear(
                grid,
                ep.edge_id,
                start_um,
                goal_um,
                ignore_labels=ignore_tuple,
                max_length_um=max_len_um,
            )
            if path.success and path.points_um:
                width_um = int(ep.width_mm * MM_TO_UM)
                grid.add_routed_polyline(
                    path.points_um,
                    width_um=width_um,
                    clearance_um=clearance_um,
                    label=f"route:{ep.edge_id}",
                )
                report.routes[ep.edge_id] = RouteOutcome(
                    edge_id=ep.edge_id,
                    polyline_um=path.points_um,
                    length_mm=path.length_mm(),
                    target_mm=ep.target_length_mm,
                    success=True,
                    rip_up_round=round_idx,
                )
                # Stash final endpoint positions (for "dragged" UV pins).
                report.final_endpoint_um[ep.start_endpoint] = path.points_um[0]
                report.final_endpoint_um[ep.goal_endpoint] = path.points_um[-1]
            else:
                attempts = failed_edges.get(ep.edge_id, 0) + 1
                failed_edges[ep.edge_id] = attempts
                if attempts <= 2 and round_idx < rip_up_rounds:
                    # Rip up the most recently routed neighbour and retry.
                    seen_ripup += 1
                    neighbour = _pick_neighbour_to_rip(report, ep)
                    if neighbour:
                        grid.remove_routed(f"route:{neighbour}")
                        # Move ripped neighbour back to the pending queue.
                        ripped = next(
                            (x for x in routes_plan if x.edge_id == neighbour), None
                        )
                        if ripped:
                            next_pending.append(ripped)
                        report.routes.pop(neighbour, None)
                    next_pending.append(ep)
                else:
                    report.routes[ep.edge_id] = RouteOutcome(
                        edge_id=ep.edge_id,
                        polyline_um=(),
                        length_mm=0.0,
                        target_mm=ep.target_length_mm,
                        success=False,
                        rip_up_round=round_idx,
                        failure_reason=path.failure_reason,
                    )
        pending = next_pending
        if not pending:
            break
    # Anything left over after the loop counts as failure.
    for ep in pending:
        if ep.edge_id not in report.routes:
            report.routes[ep.edge_id] = RouteOutcome(
                edge_id=ep.edge_id,
                polyline_um=(),
                length_mm=0.0,
                target_mm=ep.target_length_mm,
                success=False,
                rip_up_round=report.rip_up_rounds,
                failure_reason="rip_up exhausted",
            )

    logger.info(
        "skeleton routing complete: %d/%d ok, rip-up rounds=%d",
        *report.success_rate(),
        report.rip_up_rounds,
    )
    return report


def _pick_neighbour_to_rip(report: SkeletonReport, ep: EdgeRoutingPlan) -> str | None:
    """Pick the most recently routed edge as a rip-up candidate."""
    if not report.routes:
        return None
    successes = [r for r in report.routes.values() if r.success]
    if not successes:
        return None
    return successes[-1].edge_id


__all__ = [
    "EdgeRoutingPlan",
    "RouteOutcome",
    "SkeletonReport",
    "route_skeleton",
]
