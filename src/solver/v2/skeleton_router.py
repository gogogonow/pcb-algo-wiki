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
    """Build the routing plan, sorted by priority (highest first).

    Priority strategy (revised in M10c):
        1. ``rf_constrained_locked`` and short (< 6 mm) — intra-cluster
           connections must be routed first so they reserve dense space
           before wide trunks.
        2. ``rf_constrained_locked`` and long (>= 6 mm) — RF trunks.
        3. Other ``rf_constrained_*`` (free / flex) — non-critical fillers.

    Within each tier, edges are ordered by ``width × length`` descending
    (wider edges still preferred since they need more room).
    """

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
        is_locked = edge.routing_class == "rf_constrained_locked"
        is_short = (edge.target_length or manhattan) < 6.0
        # Tier base: short-locked = 1000, long-locked = 500, free = 100.
        if is_locked and is_short:
            tier_base = 1000.0
        elif is_locked:
            tier_base = 500.0
        else:
            tier_base = 100.0
        bucket_bonus = _WIDTH_GROUP_BONUS[_bucket_for_width(edge.width)]
        priority = tier_base + bucket_bonus + (edge.width or 0.5) * max(target, 1.0)
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
            # Determine which fixed components this edge touches, so we can
            # ignore routes anchored on the same component (multiple segs
            # fan out from IC1 pads and would otherwise mutually block).
            ep_components = set()
            for ep_name in (ep.start_endpoint, ep.goal_endpoint):
                if "." in ep_name:
                    ep_components.add(ep_name.split(".", 1)[0])
            # Also ignore any fixed component whose bbox encloses an endpoint
            # (e.g. a universal_node placed inside the IC1 footprint must be
            # reachable, so the IC1 obstacle has to be transparent here).
            for comp_name, comp in artifact.components.items():
                if comp.placement_kind != "fixed" or comp.bbox is None:
                    continue
                bb = comp.bbox
                for ep_name in (ep.start_endpoint, ep.goal_endpoint):
                    xy = plan.endpoint_xy.get(ep_name)
                    if xy is None:
                        continue
                    if bb.min_x <= xy[0] <= bb.max_x and bb.min_y <= xy[1] <= bb.max_y:
                        ep_components.add(comp_name)
                        break
            ignore = []
            for ep_name in (ep.start_endpoint, ep.goal_endpoint):
                tag = pad_owners.get(ep_name)
                if tag:
                    ignore.append(tag)
            for comp_name in ep_components:
                ignore.append(f"footprint:{comp_name}")
            # Also ignore any sibling routes that share an endpoint OR share
            # a fixed-component anchor with this edge (multiple microstrip
            # segs fan out from the same IC pad / IC component).
            for sibling_id, outcome in report.routes.items():
                if sibling_id == ep.edge_id or not outcome.success:
                    continue
                sibling = next(
                    (p for p in routes_plan if p.edge_id == sibling_id), None
                )
                if sibling is None:
                    continue
                sibling_eps = (sibling.start_endpoint, sibling.goal_endpoint)
                shared_endpoint = (
                    sibling.start_endpoint == ep.start_endpoint
                    or sibling.goal_endpoint == ep.start_endpoint
                    or sibling.start_endpoint == ep.goal_endpoint
                    or sibling.goal_endpoint == ep.goal_endpoint
                )
                shared_component = any(
                    "." in s and s.split(".", 1)[0] in ep_components
                    for s in sibling_eps
                )
                if shared_endpoint or shared_component:
                    ignore.append(f"route:{sibling_id}")
            ignore_tuple = tuple(ignore)
            start_um = (int(start[0] * MM_TO_UM), int(start[1] * MM_TO_UM))
            goal_um = (int(goal[0] * MM_TO_UM), int(goal[1] * MM_TO_UM))
            max_len_um: int | None = None
            if ep.target_length_mm is not None:
                # Cap upper-length to 1.3× target. The post-Phase-A meander
                # pass extends under-length routes back up to target ±tol;
                # over-length routes are harder to fix, so we constrain A*.
                # Generous upper bound: A* often needs detour space; the
                # post-Phase-A meander pass can pull under-length back up.
                max_len_um = int(ep.target_length_mm * MM_TO_UM * 3.0)
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
                    neighbour = _pick_neighbour_to_rip(report, ep, plan.endpoint_xy)
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


def _pick_neighbour_to_rip(
    report: SkeletonReport,
    ep: EdgeRoutingPlan,
    plan_endpoints: dict[str, tuple[float, float]] | None = None,
) -> str | None:
    """Pick the route most likely to be blocking *ep*.

    Strategy: among successfully routed edges, find the one whose polyline
    has the most points inside the bounding box of the failed (start, goal)
    pair (proxy for "blocks the corridor"). Falls back to the most recently
    routed edge if no spatial overlap exists.
    """
    successes = [r for r in report.routes.values() if r.success]
    if not successes:
        return None
    if plan_endpoints is None:
        return successes[-1].edge_id
    start = plan_endpoints.get(ep.start_endpoint)
    goal = plan_endpoints.get(ep.goal_endpoint)
    if start is None or goal is None:
        return successes[-1].edge_id
    sx, sy = start[0] * MM_TO_UM, start[1] * MM_TO_UM
    gx, gy = goal[0] * MM_TO_UM, goal[1] * MM_TO_UM
    box = (
        min(sx, gx) - 1000.0,
        min(sy, gy) - 1000.0,
        max(sx, gx) + 1000.0,
        max(sy, gy) + 1000.0,
    )
    best: tuple[int, str] | None = None
    for route in successes:
        hits = sum(
            1
            for px, py in route.polyline_um
            if box[0] <= px <= box[2] and box[1] <= py <= box[3]
        )
        if hits > 0 and (best is None or hits > best[0]):
            best = (hits, route.edge_id)
    return best[1] if best is not None else successes[-1].edge_id


__all__ = [
    "EdgeRoutingPlan",
    "RouteOutcome",
    "SkeletonReport",
    "route_skeleton",
]
