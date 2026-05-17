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


def _orientation_unit(orient_deg: float) -> tuple[float, float]:
    """Convert orientation in degrees (CCW from +x) to a unit vector."""
    import math as _m

    rad = _m.radians(orient_deg)
    return (_m.cos(rad), _m.sin(rad))


def _build_pin_orientations(
    artifact: FrontendArtifact,
) -> dict[str, tuple[float, float]]:
    """Map ``"Component.PIN_N" -> (ux, uy)`` for fixed pads with orientation.

    Used to force the first leg of a microstrip route to escape along the
    pad's local_orientation, matching the physical pad anatomy (a pin
    pointing south must launch its trace going south, not diagonally).
    """
    out: dict[str, tuple[float, float]] = {}
    for comp_name, comp in artifact.components.items():
        if comp.placement_kind != "fixed":
            continue
        for pad in comp.pads:
            if pad.orientation is None:
                continue
            key = f"{comp_name}.{pad.pin}"
            out[key] = _orientation_unit(pad.orientation)
    return out


def _board_frame_inward(
    xy: tuple[float, float],
    board_w: float,
    board_h: float,
    tol: float = 0.05,
) -> tuple[float, float] | None:
    """If ``xy`` sits on a board frame, return the inward unit normal."""
    x, y = xy
    if abs(y) <= tol:
        return (0.0, 1.0)
    if abs(y - board_h) <= tol:
        return (0.0, -1.0)
    if abs(x) <= tol:
        return (1.0, 0.0)
    if abs(x - board_w) <= tol:
        return (-1.0, 0.0)
    return None


def _compute_escape_stub(
    endpoint: str,
    xy: tuple[float, float],
    width_mm: float,
    pin_orient: dict[str, tuple[float, float]],
    board_w: float,
    board_h: float,
    clearance_mm: float,
    other_xy: tuple[float, float] | None = None,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return (stub_anchor_xy_mm, unit_dir) for a forced escape leg.

    Priority:
      1. IC fixed pad with known orientation whose direction agrees with
         the bearing to the other endpoint → escape along orientation.
      2. Endpoint sits on board frame → escape inward along the normal
         (regardless of any stale pin orientation that may point outward).
      3. Otherwise no stub.

    ``other_xy`` is the opposite endpoint of the same edge. If supplied,
    we filter out pin orientations that would force a U-turn (orientation
    dot bearing < 0) — those usually indicate that the universal node was
    placed against the pin axis and adding a stub would only hurt.
    """
    frame_dir = _board_frame_inward(xy, board_w, board_h)
    direction = pin_orient.get(endpoint)
    if direction is not None and other_xy is not None:
        bearing = (other_xy[0] - xy[0], other_xy[1] - xy[1])
        bearing_len = (bearing[0] ** 2 + bearing[1] ** 2) ** 0.5
        if bearing_len > 1e-6:
            dot = (direction[0] * bearing[0] + direction[1] * bearing[1]) / bearing_len
            if dot < 0.2:
                # Orientation disagrees with reach direction; skip stub.
                direction = None
    if direction is None:
        direction = frame_dir
    elif frame_dir is not None:
        # Endpoint on board frame: prefer inward frame normal so the trace
        # never starts by skimming along (or off) the board edge.
        if direction[0] * frame_dir[0] + direction[1] * frame_dir[1] < 0.2:
            direction = frame_dir
    if direction is None:
        return None
    base_stub = max(width_mm * 2.0, clearance_mm + 0.6, 1.2)
    if other_xy is not None:
        dist = ((other_xy[0] - xy[0]) ** 2 + (other_xy[1] - xy[1]) ** 2) ** 0.5
        # Cap stub at 1/3 of the start→goal distance so we never overshoot.
        base_stub = min(base_stub, max(0.6, dist * 0.33))
    stub_len = base_stub
    anchor = (xy[0] + direction[0] * stub_len, xy[1] + direction[1] * stub_len)
    return anchor, direction


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


def _simplify_collinear(pts: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    """Drop interior points that are collinear with their neighbours."""
    if len(pts) <= 2:
        return list(pts)
    out: list[tuple[int, int]] = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        # Cross product of (b-a) x (c-b); zero means collinear.
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross != 0:
            out.append(pts[i])
    out.append(pts[-1])
    return out


def _chamfer_90deg_corners(
    pts: tuple[tuple[int, int], ...], chamfer_um: int
) -> tuple[tuple[int, int], ...]:
    """Insert 45° chamfer at every orthogonal corner.

    The router's octilinear A* produces 8-direction polylines; 90° turns
    occur where two axis-aligned segments meet perpendicularly. Each such
    corner is replaced by two 45° turns (a triangular cap of side
    ``chamfer_um``).
    """
    if len(pts) < 3 or chamfer_um <= 0:
        return pts
    simplified = _simplify_collinear(pts)
    out: list[tuple[int, int]] = [simplified[0]]
    for i in range(1, len(simplified) - 1):
        ax, ay = simplified[i - 1]
        bx, by = simplified[i]
        cx, cy = simplified[i + 1]
        dx1, dy1 = bx - ax, by - ay
        dx2, dy2 = cx - bx, cy - by
        is_axial1 = (dx1 == 0) ^ (dy1 == 0)
        is_axial2 = (dx2 == 0) ^ (dy2 == 0)
        # Only chamfer 90° corners formed by two axial segments.
        if is_axial1 and is_axial2 and (dx1 * dx2 + dy1 * dy2 == 0):
            len1 = abs(dx1) + abs(dy1)
            len2 = abs(dx2) + abs(dy2)
            cham = min(chamfer_um, len1 // 2, len2 // 2)
            if cham <= 0:
                out.append((bx, by))
                continue
            sx1 = 1 if dx1 > 0 else (-1 if dx1 < 0 else 0)
            sy1 = 1 if dy1 > 0 else (-1 if dy1 < 0 else 0)
            sx2 = 1 if dx2 > 0 else (-1 if dx2 < 0 else 0)
            sy2 = 1 if dy2 > 0 else (-1 if dy2 < 0 else 0)
            # Approach point: pull back from corner along incoming dir.
            out.append((bx - sx1 * cham, by - sy1 * cham))
            # Departure point: step away from corner along outgoing dir.
            out.append((bx + sx2 * cham, by + sy2 * cham))
        else:
            out.append((bx, by))
    out.append(simplified[-1])
    return tuple(out)


def _max_corner_angle_deviation(pts: tuple[tuple[int, int], ...]) -> float:
    """Return the maximum |cos(theta)| where theta is the deviation from 45°
    multiples at any interior corner. Zero means all corners are 45° / 90°
    multiples (which combined with chamfering means all 45°). For diagnostic
    use in tests."""
    import math

    worst = 0.0
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        d1 = math.hypot(bx - ax, by - ay)
        d2 = math.hypot(cx - bx, cy - by)
        if d1 < 1e-6 or d2 < 1e-6:
            continue
        ux = (bx - ax) / d1
        uy = (by - ay) / d1
        vx = (cx - bx) / d2
        vy = (cy - by) / d2
        cos_t = max(-1.0, min(1.0, ux * vx + uy * vy))
        theta = math.degrees(math.acos(cos_t))
        # Deviation from nearest 45° multiple (0, 45, 90, 135, 180).
        dev = min(abs(theta - k * 45) for k in range(5))
        worst = max(worst, dev)
    return worst


def _polyline_len_mm(pts: tuple[tuple[int, int], ...]) -> float:
    if len(pts) < 2:
        return 0.0
    import math

    total = 0.0
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        total += math.hypot(bx - ax, by - ay)
    return total / MM_TO_UM


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
    pin_orient = _build_pin_orientations(artifact)
    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))

    report = SkeletonReport()
    pending = list(routes_plan)
    route_plan_by_id = {p.edge_id: p for p in routes_plan}
    endpoint_to_route_ids = _build_endpoint_to_route_ids(routes_plan)
    seen_ripup = 0
    failed_edges: dict[str, int] = {}  # edge_id -> attempts
    clearance_um = int(clearance_mm * MM_TO_UM)

    for round_idx in range(rip_up_rounds + 1):
        report.rip_up_rounds = round_idx
        next_pending: list[EdgeRoutingPlan] = []
        for ep in pending:
            edge_overrides = plan.edge_endpoint_xy.get(ep.edge_id, {})
            start = edge_overrides.get(
                ep.start_endpoint, plan.endpoint_xy.get(ep.start_endpoint)
            )
            goal = edge_overrides.get(
                ep.goal_endpoint, plan.endpoint_xy.get(ep.goal_endpoint)
            )
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
                    xy = edge_overrides.get(ep_name, plan.endpoint_xy.get(ep_name))
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
            # Also ignore any sibling routes that share an endpoint.
            sibling_ids = endpoint_to_route_ids.get(ep.start_endpoint, set()).union(
                endpoint_to_route_ids.get(ep.goal_endpoint, set())
            )
            for sibling_id in sibling_ids:
                if sibling_id == ep.edge_id:
                    continue
                outcome = report.routes.get(sibling_id)
                if outcome is None or not outcome.success:
                    continue
                ignore.append(f"route:{sibling_id}")
            ignore_tuple = tuple(ignore)
            # Forced escape stubs: IC pins exit along local_orientation;
            # board-frame terminals exit inward along the normal. The A*
            # search runs between the stub anchors; we prepend / append the
            # straight stub leg afterwards so the trace honours pin anatomy
            # and never crosses the board frame.
            start_stub = _compute_escape_stub(
                ep.start_endpoint,
                start,
                ep.width_mm,
                pin_orient,
                board_w,
                board_h,
                clearance_mm,
                other_xy=goal,
            )
            goal_stub = _compute_escape_stub(
                ep.goal_endpoint,
                goal,
                ep.width_mm,
                pin_orient,
                board_w,
                board_h,
                clearance_mm,
                other_xy=start,
            )
            astar_start = start_stub[0] if start_stub else start
            astar_goal = goal_stub[0] if goal_stub else goal
            start_um = (
                int(astar_start[0] * MM_TO_UM),
                int(astar_start[1] * MM_TO_UM),
            )
            goal_um = (
                int(astar_goal[0] * MM_TO_UM),
                int(astar_goal[1] * MM_TO_UM),
            )
            # If the stub anchor is blocked under the active ignore set, drop
            # the stub (avoid letting A* snap to a far-away free cell which
            # would create overshoots).
            if start_stub and grid.is_blocked(*start_um, ignore_labels=ignore_tuple):
                start_stub = None
                start_um = (int(start[0] * MM_TO_UM), int(start[1] * MM_TO_UM))
            if goal_stub and grid.is_blocked(*goal_um, ignore_labels=ignore_tuple):
                goal_stub = None
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
                pts = list(path.points_um)
                # Prepend / append straight stub legs so the trace exits the
                # pad along its physical orientation (or inward from a
                # board frame). Without this, A* may diverge diagonally
                # from the very first cell.
                if start_stub is not None:
                    # Align the real start onto the stub's axial line by
                    # snapping the perpendicular coordinate to the A* start.
                    s_dir = start_stub[1]
                    if abs(s_dir[0]) < 1e-6:  # vertical stub
                        real_start_um = (pts[0][0], int(start[1] * MM_TO_UM))
                    elif abs(s_dir[1]) < 1e-6:  # horizontal stub
                        real_start_um = (int(start[0] * MM_TO_UM), pts[0][1])
                    else:
                        real_start_um = (
                            int(start[0] * MM_TO_UM),
                            int(start[1] * MM_TO_UM),
                        )
                    if pts and pts[0] != real_start_um:
                        pts.insert(0, real_start_um)
                if goal_stub is not None:
                    g_dir = goal_stub[1]
                    if abs(g_dir[0]) < 1e-6:
                        real_goal_um = (pts[-1][0], int(goal[1] * MM_TO_UM))
                    elif abs(g_dir[1]) < 1e-6:
                        real_goal_um = (int(goal[0] * MM_TO_UM), pts[-1][1])
                    else:
                        real_goal_um = (
                            int(goal[0] * MM_TO_UM),
                            int(goal[1] * MM_TO_UM),
                        )
                    if pts and pts[-1] != real_goal_um:
                        pts.append(real_goal_um)
                stubbed = tuple(pts)
                # Strict 45°: chamfer 90° corners. Chamfer size = max(width,
                # 2 grid steps) so the triangular cap is always visible.
                chamfer_um = max(width_um, config.step_um * 2)
                chamfered = _chamfer_90deg_corners(stubbed, chamfer_um)
                grid.add_routed_polyline(
                    chamfered,
                    width_um=width_um,
                    clearance_um=clearance_um,
                    label=f"route:{ep.edge_id}",
                )
                report.routes[ep.edge_id] = RouteOutcome(
                    edge_id=ep.edge_id,
                    polyline_um=chamfered,
                    length_mm=_polyline_len_mm(chamfered),
                    target_mm=ep.target_length_mm,
                    success=True,
                    rip_up_round=round_idx,
                )
                # Stash final endpoint positions (for "dragged" UV pins).
                report.final_endpoint_um[ep.start_endpoint] = chamfered[0]
                report.final_endpoint_um[ep.goal_endpoint] = chamfered[-1]
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
                        ripped = route_plan_by_id.get(neighbour)
                        report.routes.pop(neighbour, None)
                        if ripped:
                            next_pending.append(ripped)
                        next_pending.append(ep)
                    else:
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


def _build_endpoint_to_route_ids(
    routes_plan: list[EdgeRoutingPlan],
) -> dict[str, set[str]]:
    endpoint_to_route_ids: dict[str, set[str]] = {}
    for route_plan in routes_plan:
        endpoint_to_route_ids.setdefault(route_plan.start_endpoint, set()).add(
            route_plan.edge_id
        )
        endpoint_to_route_ids.setdefault(route_plan.goal_endpoint, set()).add(
            route_plan.edge_id
        )
    return endpoint_to_route_ids


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
