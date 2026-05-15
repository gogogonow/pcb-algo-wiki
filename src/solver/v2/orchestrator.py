"""End-to-end orchestrator for the M10 v2 skeleton-first pipeline.

Calls Frontend Compiler (reused), then Phase A (skeleton routing),
Phase B (UV adhesion), and Phase C (flexible_path / floating leftovers,
delegated to ``solver.astar_flex`` if any apply). Returns a bundle that
mirrors :class:`solver.OrchestratorResult` so downstream postproc and
SVG rendering can be reused unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from frontend.compile import compile_layout
from frontend.models import FrontendArtifact
from frontend.solver_ir import compile_solver_ir
from postproc.meander import apply_meanders
from schema.geometry_ir import (
    ComponentPlacement,
    GeometryIR,
    PinPlacement,
    RoutePolyline,
)
from schema.v6_ir import Board, Point, RoutingClass
from solver.astar_flex import AstarConfig, route_flexible_paths
from solver.units import MM_TO_UM

from .channel_grid import GridConfig
from .node_planner import NodePlan, plan_node_positions
from .skeleton_router import RouteOutcome, SkeletonReport, route_skeleton
from .uv_adhesion import UvAdhesionReport, adhere_uv_components


@dataclass
class OrchestratorV2Options:
    clearance_mm: float = 0.15
    grid_step_um: int = 100
    rip_up_rounds: int = 5
    persist_phase_outputs: bool = True
    out_dir: Path = field(default_factory=lambda: Path("out"))


@dataclass
class PhaseAResult:
    skeleton: SkeletonReport
    plan: NodePlan
    wall_seconds: float


@dataclass
class PhaseBResult:
    adhesion: UvAdhesionReport
    wall_seconds: float


@dataclass
class PhaseCResult:
    routed_flex_edges: list[str]
    wall_seconds: float
    failed_flex_edges: list[str] = field(default_factory=list)


@dataclass
class OrchestratorV2Result:
    artifact: FrontendArtifact
    phase_a: PhaseAResult
    phase_b: PhaseBResult
    phase_c: PhaseCResult
    geometry: GeometryIR

    @property
    def status(self) -> str:
        return self.geometry.solve_status


def _inject_prea_endpoints(
    yaml_path: str | Path,
    artifact: FrontendArtifact,
    plan: NodePlan,
    board_w: float,
    board_h: float,
) -> NodePlan:
    """Refine ``plan.endpoint_xy`` using the PreA target_length-aware solver.

    Lazy-imports :mod:`tools.pcb_solve_v2` to avoid a circular dependency
    (pcb_solve_v2 imports the orchestrator at module top-level). Failures are
    swallowed so the router still has the heuristic seeds to fall back on.
    """
    try:
        from tools.pcb_solve_v2 import (
            _load_branch_offset_u_tokens,
            _load_junction_templates,
            solve_pre_a_from_artifact,
        )

        templates = _load_junction_templates(layout_path=Path(yaml_path))
        tokens = _load_branch_offset_u_tokens(layout_path=Path(yaml_path))
        positions, _ = solve_pre_a_from_artifact(
            artifact,
            plan.endpoint_xy,
            board_w=board_w,
            board_h=board_h,
            junction_templates=templates,
            branch_offset_u_tokens=tokens,
        )
    except Exception:
        return plan

    for endpoint, xy in positions.items():
        plan.endpoint_xy[endpoint] = xy
    return plan


def solve_layout_v2(
    yaml_path: str | Path,
    options: OrchestratorV2Options | None = None,
) -> OrchestratorV2Result:
    options = options or OrchestratorV2Options()
    artifact = compile_layout(str(yaml_path))

    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))

    # ---- Phase A: skeleton routing -----------------------------------------
    t0 = time.perf_counter()
    plan = plan_node_positions(
        artifact, board_width_mm=board_w, board_height_mm=board_h
    )
    # Inject preA-refined endpoints (target_length aware) before A* router.
    plan = _inject_prea_endpoints(yaml_path, artifact, plan, board_w, board_h)
    grid_cfg = GridConfig(step_um=options.grid_step_um)
    skeleton = route_skeleton(
        artifact,
        plan,
        clearance_mm=options.clearance_mm,
        grid_config=grid_cfg,
        rip_up_rounds=options.rip_up_rounds,
    )
    phase_a_wall = time.perf_counter() - t0

    # ---- Length compensation: M7 hairpin meander on under-length routes ---
    skeleton = _apply_length_compensation(yaml_path, artifact, skeleton)

    # ---- Phase B: UV adhesion ---------------------------------------------
    t1 = time.perf_counter()
    adhesion = adhere_uv_components(
        artifact,
        skeleton,
        seed_anchor_mm=plan.uv_anchor_seed,
        seed_rotation_deg=plan.uv_rotation_seed,
    )
    phase_b_wall = time.perf_counter() - t1
    skeleton = _retry_failed_phase_a_routes(
        artifact=artifact,
        plan=plan,
        skeleton=skeleton,
        options=options,
    )

    # ---- Phase C: A* on flexible_path edges (no-op when none) ------------
    t2 = time.perf_counter()
    geometry = _assemble_geometry(
        artifact=artifact,
        plan=plan,
        skeleton=skeleton,
        adhesion=adhesion,
        wall_seconds=phase_a_wall + phase_b_wall,
    )
    flex_routed: list[str] = []
    flex_failed: list[str] = []
    try:
        ir = compile_solver_ir(yaml_path)
    except Exception:
        ir = None
    if ir is not None:
        geometry, flex_routed, flex_failed = _route_flex_edges(
            ir=ir, artifact=artifact, plan=plan, geometry=geometry
        )
    phase_c_wall = time.perf_counter() - t2

    # Refresh wall in the final GeometryIR.
    geometry = _with_wall_seconds(geometry, phase_a_wall + phase_b_wall + phase_c_wall)

    return OrchestratorV2Result(
        artifact=artifact,
        phase_a=PhaseAResult(skeleton=skeleton, plan=plan, wall_seconds=phase_a_wall),
        phase_b=PhaseBResult(adhesion=adhesion, wall_seconds=phase_b_wall),
        phase_c=PhaseCResult(
            routed_flex_edges=flex_routed,
            failed_flex_edges=flex_failed,
            wall_seconds=phase_c_wall,
        ),
        geometry=geometry,
    )


def _route_flex_edges(
    *,
    ir: object,
    artifact: FrontendArtifact,
    plan: NodePlan,
    geometry: GeometryIR,
    grid_step_um: int = 500,
) -> tuple[GeometryIR, list[str], list[str]]:
    """Route every ``flexible_path`` edge using ``solver.astar_flex``.

    Skeleton router only handles ``rf_constrained*`` edges, so flex edges are
    absent from ``geometry.routes``. We seed each flex edge with a direct
    two-point polyline using node_planner endpoints, then delegate to
    :func:`solver.astar_flex.route_flexible_paths` which replaces the seeds
    with obstacle-aware A* polylines.
    """

    flex_edge_ids = [
        eid
        for eid, edge in artifact.edges.items()
        if edge.routing_class == "flexible_path"
    ]
    if not flex_edge_ids:
        return geometry, [], []

    seeded_routes = dict(geometry.routes)
    for eid in flex_edge_ids:
        edge = artifact.edges[eid]
        if len(edge.connections) < 2:
            continue
        a, b = edge.connections[0], edge.connections[-1]
        a_xy = plan.endpoint_xy.get(a)
        b_xy = plan.endpoint_xy.get(b)
        if a_xy is None or b_xy is None:
            continue
        seeded_routes[eid] = RoutePolyline(
            edge_id=eid,
            routing_class=RoutingClass.FLEXIBLE_PATH,
            width=float(edge.width or 0.2),
            points=(
                Point(x=a_xy[0], y=a_xy[1]),
                Point(x=b_xy[0], y=b_xy[1]),
            ),
        )

    seeded_geom = GeometryIR(
        project=geometry.project,
        board=geometry.board,
        placements=geometry.placements,
        routes=seeded_routes,
        nodes=geometry.nodes,
        solve_status=geometry.solve_status,
        solve_wall_seconds=geometry.solve_wall_seconds,
        objective_value=geometry.objective_value,
    )

    try:
        new_geom, report = route_flexible_paths(
            ir=ir,  # type: ignore[arg-type]
            artifact=artifact,
            geom=seeded_geom,
            config=AstarConfig(grid_step_um=grid_step_um),
        )
    except Exception:
        return seeded_geom, [], list(flex_edge_ids)

    return new_geom, list(report.routed_edges), list(report.failed_edges)


def _retry_failed_phase_a_routes(
    *,
    artifact: FrontendArtifact,
    plan: NodePlan,
    skeleton: SkeletonReport,
    options: OrchestratorV2Options,
) -> SkeletonReport:
    """Retry only failed Phase-A edges with relaxed routing parameters."""
    failed_edges = [
        edge_id for edge_id, route in skeleton.routes.items() if not route.success
    ]
    if not failed_edges:
        return skeleton

    base_grid = GridConfig(step_um=options.grid_step_um)
    retry_grid = GridConfig(
        step_um=base_grid.step_um * 2,
        turn_penalty_um=base_grid.turn_penalty_um,
        near_obstacle_penalty_um=base_grid.near_obstacle_penalty_um,
        max_expansions=base_grid.max_expansions * 5,
    )
    retry_report = route_skeleton(
        artifact,
        plan,
        clearance_mm=max(options.clearance_mm * 0.5, 0.01),
        grid_config=retry_grid,
        rip_up_rounds=options.rip_up_rounds,
    )

    merged_routes = dict(skeleton.routes)
    merged_endpoints = dict(skeleton.final_endpoint_um)
    improved = False
    for edge_id in failed_edges:
        retried = retry_report.routes.get(edge_id)
        if retried is None or not retried.success:
            continue
        merged_routes[edge_id] = retried
        edge = artifact.edges.get(edge_id)
        if edge is not None:
            for endpoint in edge.connections:
                endpoint_xy = retry_report.final_endpoint_um.get(endpoint)
                if endpoint_xy is not None:
                    merged_endpoints[endpoint] = endpoint_xy
        improved = True

    if not improved:
        return skeleton
    return SkeletonReport(
        routes=merged_routes,
        final_endpoint_um=merged_endpoints,
        rip_up_rounds=max(skeleton.rip_up_rounds, retry_report.rip_up_rounds),
    )


def _with_wall_seconds(geometry: GeometryIR, wall_seconds: float) -> GeometryIR:
    return GeometryIR(
        project=geometry.project,
        board=geometry.board,
        placements=geometry.placements,
        routes=geometry.routes,
        nodes=geometry.nodes,
        solve_status=geometry.solve_status,
        solve_wall_seconds=float(max(wall_seconds, 0.0)),
        objective_value=geometry.objective_value,
    )


def _assemble_geometry(
    *,
    artifact: FrontendArtifact,
    plan: NodePlan,
    skeleton: SkeletonReport,
    adhesion: UvAdhesionReport,
    wall_seconds: float,
) -> GeometryIR:
    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))
    origin = artifact.board.get("origin", {}) or {}
    board = Board(
        width=board_w,
        height=board_h,
        origin=Point(
            x=float(origin.get("x", 0.0)),
            y=float(origin.get("y", 0.0)),
        ),
    )
    placements: dict[str, ComponentPlacement] = {}
    # Fixed components.
    for comp_name, comp in artifact.components.items():
        if comp.placement_kind != "fixed":
            continue
        pads = []
        for pad in comp.pads:
            if pad.abs_x is None or pad.abs_y is None:
                continue
            pads.append(
                PinPlacement(pin=pad.pin, point=Point(x=pad.abs_x, y=pad.abs_y))
            )
        # Best effort — derive placement origin from the first pad if none
        # is recorded. We use the V33 placement.x/y when available later.
        anchor_x = sum(p.point.x for p in pads) / len(pads) if pads else 0.0
        anchor_y = sum(p.point.y for p in pads) / len(pads) if pads else 0.0
        # Try to recover the original anchor from frontend. We don't have it
        # in ComponentExpansion, so use centroid.
        placements[comp_name] = ComponentPlacement(
            component=comp_name,
            anchor=Point(x=anchor_x, y=anchor_y),
            rotation_deg=0.0,
            pads=tuple(pads),
        )
    # UV components (overrides).
    placements.update(adhesion.placements)

    routes: dict[str, RoutePolyline] = {}
    for edge_id, route in skeleton.routes.items():
        if not route.success or len(route.polyline_um) < 2:
            continue
        edge = artifact.edges.get(edge_id)
        if edge is None:
            continue
        rclass = _routing_class(edge.routing_class)
        width = float(edge.width or 0.5)
        pts = tuple(Point(x=x / MM_TO_UM, y=y / MM_TO_UM) for x, y in route.polyline_um)
        routes[edge_id] = RoutePolyline(
            edge_id=edge_id,
            routing_class=rclass,
            width=width,
            points=pts,
        )

    nodes: dict[str, Point] = {}
    for node_name in artifact.nodes:
        xy = skeleton.final_endpoint_um.get(node_name) or _seed_um(
            plan.endpoint_xy.get(node_name)
        )
        if xy is None:
            continue
        nodes[node_name] = Point(x=xy[0] / MM_TO_UM, y=xy[1] / MM_TO_UM)

    ok, total = skeleton.success_rate()
    status = (
        "OPTIMAL"
        if (total > 0 and ok == total)
        else ("FEASIBLE" if ok > 0 else "INFEASIBLE")
    )

    return GeometryIR(
        project=artifact.project_name or "unknown",
        board=board,
        placements=placements,
        routes=routes,
        nodes=nodes,
        solve_status=status,
        solve_wall_seconds=float(max(wall_seconds, 0.0)),
        objective_value=None,
    )


def _routing_class(name: str) -> RoutingClass:
    try:
        return RoutingClass(name)
    except ValueError:
        return RoutingClass.RF_CONSTRAINED_FREE


def _apply_length_compensation(
    yaml_path: str | Path,
    artifact: FrontendArtifact,
    skeleton: SkeletonReport,
) -> SkeletonReport:
    """Apply M7 hairpin meander to skeleton routes whose length is below target.

    Builds a minimal GeometryIR from the current skeleton, calls
    :func:`postproc.meander.apply_meanders`, then writes any modified
    polylines back into a fresh :class:`SkeletonReport`. Routes that already
    meet target or have no target are passed through unchanged.
    """

    under_length = [
        rid
        for rid, r in skeleton.routes.items()
        if r.success and r.target_mm is not None and r.length_mm < r.target_mm - 0.15
    ]
    if not under_length:
        return skeleton

    try:
        ir = compile_solver_ir(yaml_path)
    except Exception:
        return skeleton

    routes_ir: dict[str, RoutePolyline] = {}
    for edge_id, r in skeleton.routes.items():
        if not r.success or len(r.polyline_um) < 2:
            continue
        edge = artifact.edges.get(edge_id)
        if edge is None:
            continue
        pts = tuple(Point(x=x / MM_TO_UM, y=y / MM_TO_UM) for x, y in r.polyline_um)
        routes_ir[edge_id] = RoutePolyline(
            edge_id=edge_id,
            routing_class=_routing_class(edge.routing_class),
            width=float(edge.width or 0.5),
            points=pts,
        )

    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))
    geom = GeometryIR(
        project=artifact.project_name or "unknown",
        board=Board(width=board_w, height=board_h, origin=Point(x=0.0, y=0.0)),
        placements={},
        routes=routes_ir,
        nodes={},
        solve_status="FEASIBLE",
        solve_wall_seconds=0.0,
        objective_value=None,
    )

    try:
        new_geom, _report = apply_meanders(geom, ir)
    except Exception:
        return skeleton

    new_routes: dict[str, RouteOutcome] = {}
    for edge_id, outcome in skeleton.routes.items():
        new_route = new_geom.routes.get(edge_id)
        if (
            new_route is None
            or new_route.points == routes_ir.get(edge_id, new_route).points
        ):
            new_routes[edge_id] = outcome
            continue
        new_poly = tuple(
            (int(round(p.x * MM_TO_UM)), int(round(p.y * MM_TO_UM)))
            for p in new_route.points
        )
        new_len = _polyline_length_mm(new_poly)
        new_routes[edge_id] = RouteOutcome(
            edge_id=outcome.edge_id,
            polyline_um=new_poly,
            length_mm=new_len,
            target_mm=outcome.target_mm,
            success=outcome.success,
            rip_up_round=outcome.rip_up_round,
            failure_reason=outcome.failure_reason,
        )

    return SkeletonReport(
        routes=new_routes,
        final_endpoint_um=dict(skeleton.final_endpoint_um),
        rip_up_rounds=skeleton.rip_up_rounds,
    )


def _polyline_length_mm(points: tuple[tuple[int, int], ...]) -> float:
    if len(points) < 2:
        return 0.0
    total_um = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx = bx - ax
        dy = by - ay
        total_um += (dx * dx + dy * dy) ** 0.5
    return total_um / MM_TO_UM


def _seed_um(xy: tuple[float, float] | None) -> tuple[int, int] | None:
    if xy is None:
        return None
    return (int(xy[0] * MM_TO_UM), int(xy[1] * MM_TO_UM))


def phase_summary(result: OrchestratorV2Result) -> dict[str, object]:
    """Compact summary suitable for JSON dumps and CI assertions."""
    ok, total = result.phase_a.skeleton.success_rate()
    routes = result.phase_a.skeleton.routes
    err_pcts = [
        abs(r.length_err_pct) for r in routes.values() if r.length_err_pct is not None
    ]
    return {
        "project": result.geometry.project,
        "status": result.geometry.solve_status,
        "phase_a": {
            "routed": ok,
            "total": total,
            "rip_up_rounds": result.phase_a.skeleton.rip_up_rounds,
            "wall_s": result.phase_a.wall_seconds,
            "max_len_err_pct": max(err_pcts) if err_pcts else 0.0,
            "failed": [
                {"edge_id": r.edge_id, "reason": r.failure_reason}
                for r in routes.values()
                if not r.success
            ],
        },
        "phase_b": {
            "uv_placed": len(result.phase_b.adhesion.placements),
            "uv_total": len(result.artifact.uv_components),
            "wall_s": result.phase_b.wall_seconds,
            "failed": [
                {"uv": uv, "reason": reason}
                for uv, reason in result.phase_b.adhesion.failed
            ],
        },
        "phase_c": {
            "flex_routed": len(result.phase_c.routed_flex_edges),
            "flex_failed": list(result.phase_c.failed_flex_edges),
            "wall_s": result.phase_c.wall_seconds,
        },
        "wall_total_s": result.geometry.solve_wall_seconds,
    }


__all__ = [
    "_assemble_geometry",
    "OrchestratorV2Options",
    "OrchestratorV2Result",
    "PhaseAResult",
    "PhaseBResult",
    "PhaseCResult",
    "phase_summary",
    "solve_layout_v2",
]
