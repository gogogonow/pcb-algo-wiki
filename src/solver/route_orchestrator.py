"""M9 — Octilinear router orchestrator with rip-up-and-reroute.

Replaces the bbox-based "single shot" placement-then-extract pipeline with a
priority-ordered routing loop:

1. Sort edges: ``rf_constrained_locked`` first (longest target first), then
   ``rf_constrained_free``, then ``flexible_path``.  Ties broken by edge id
   for stable behaviour.
2. For each edge, build an :class:`~solver.obstacle_map.ObstacleMap` from the
   already-routed polylines and call :func:`solver.astar_octilinear.search`.
3. On failure, try to identify *blocker* edges whose footprint sits in the
   start↔goal axis-aligned bounding box; rip them up and retry. Each edge is
   capped at ``MAX_RIPUP_PER_EDGE`` rip-ups to bound the loop.
4. After ``max_rounds`` rounds any still-unrouted edges are returned in
   :attr:`RouteOrchestratorReport.unrouted` for the upstream pipeline to
   surface as warnings (and to fall back to the M4-style straight polyline).

The orchestrator is intentionally permissive — when an edge stays unrouted it
keeps the original (CP-SAT extracted) two-point polyline so the GeometryIR
remains well-formed and downstream postproc / SVG render still work.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from frontend.models import FrontendArtifact
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverIR

from .astar_octilinear import OctilinearConfig, OctilinearResult, search
from .obstacle_map import (
    DEFAULT_GRID_STEP_UM,
    DEFAULT_PAD_HALO_UM,
    build_obstacle_map,
)
from .units import length_tolerance_um

DEFAULT_MAX_ROUNDS = 10
DEFAULT_MAX_RIPUP_PER_EDGE = 3

_PRIORITY = {
    RoutingClass.RF_CONSTRAINED_LOCKED: 0,
    RoutingClass.RF_CONSTRAINED_FREE: 1,
    RoutingClass.FLEXIBLE_PATH: 2,
}


@dataclass(frozen=True)
class RouteOrchestratorConfig:
    grid_step_um: int = DEFAULT_GRID_STEP_UM
    pad_halo_um: int = DEFAULT_PAD_HALO_UM
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_ripup_per_edge: int = DEFAULT_MAX_RIPUP_PER_EDGE
    octilinear: OctilinearConfig = field(default_factory=OctilinearConfig)


@dataclass
class RouteOrchestratorReport:
    routed: list[str] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    rounds_used: int = 0
    ripup_count: dict[str, int] = field(default_factory=dict)
    overshoot_edges: list[str] = field(default_factory=list)
    expansions_total: int = 0


def _sort_edges(geom: GeometryIR, ir: SolverIR) -> list[str]:
    keys: list[tuple[int, float, str]] = []
    for edge_id, route in geom.routes.items():
        edge = ir.edges.get(edge_id)
        if edge is None:
            continue
        prio = _PRIORITY.get(edge.routing_class, 99)
        target = -float(edge.target_length or 0.0)
        keys.append((prio, target, edge_id))
    keys.sort()
    return [k[2] for k in keys]


def _find_blockers(
    edge_id: str,
    geom: GeometryIR,
    routed: dict[str, RoutePolyline],
) -> list[str]:
    """Return routed edges whose bbox intersects the active edge's bbox."""
    target = geom.routes.get(edge_id)
    if target is None or not routed:
        return []
    pts = target.points
    tx_lo = min(p.x for p in pts)
    ty_lo = min(p.y for p in pts)
    tx_hi = max(p.x for p in pts)
    ty_hi = max(p.y for p in pts)
    blockers: list[str] = []
    for other_id, other in routed.items():
        if other_id == edge_id:
            continue
        ox_lo = min(p.x for p in other.points)
        oy_lo = min(p.y for p in other.points)
        ox_hi = max(p.x for p in other.points)
        oy_hi = max(p.y for p in other.points)
        if tx_hi < ox_lo or ox_hi < tx_lo or ty_hi < oy_lo or oy_hi < ty_lo:
            continue
        blockers.append(other_id)
    return blockers


def _route_one(
    edge_id: str,
    geom: GeometryIR,
    ir: SolverIR,
    artifact: FrontendArtifact,
    routed: dict[str, RoutePolyline],
    cfg: RouteOrchestratorConfig,
) -> OctilinearResult:
    original = geom.routes[edge_id]
    edge = ir.edges[edge_id]
    start_pt = original.points[0]
    end_pt = original.points[-1]
    skip_terms = frozenset(edge.endpoints)
    skip_comps: set[str] = set()
    for term_key in edge.endpoints:
        term = ir.terminals.get(term_key)
        if term is None:
            continue
        comp_name = term_key.split(".")[0]
        if comp_name in artifact.components or comp_name in artifact.uv_components:
            skip_comps.add(comp_name)
    edge_width_mm = (
        float(edge.width) if (edge is not None and edge.width is not None) else None
    )
    obstacles = build_obstacle_map(
        ir=ir,
        artifact=artifact,
        routed=routed,
        step_um=cfg.grid_step_um,
        pad_halo_um=cfg.pad_halo_um,
        active_edge_width_mm=edge_width_mm,
        skip_terminals=skip_terms,
        skip_components=frozenset(skip_comps),
    )
    target_length_mm = (
        float(edge.target_length)
        if edge.routing_class is RoutingClass.RF_CONSTRAINED_LOCKED
        and edge.target_length is not None
        else None
    )
    tol_um = (
        length_tolerance_um(target_length_mm) if target_length_mm is not None else 0
    )
    return search(
        start_xy_mm=(float(start_pt.x), float(start_pt.y)),
        goal_xy_mm=(float(end_pt.x), float(end_pt.y)),
        obstacles=obstacles,
        config=cfg.octilinear,
        target_length_mm=target_length_mm,
        length_tol_um=tol_um,
    )


def route_all(
    *,
    ir: SolverIR,
    artifact: FrontendArtifact,
    geom: GeometryIR,
    config: RouteOrchestratorConfig | None = None,
) -> tuple[GeometryIR, RouteOrchestratorReport]:
    """Route every edge in ``geom`` octilinearly with rip-up-and-reroute.

    Returns a new :class:`GeometryIR` whose ``routes`` are replaced by the
    A*-derived polylines (original two-point routes are kept for unrouted
    edges) plus a :class:`RouteOrchestratorReport`.
    """
    cfg = config or RouteOrchestratorConfig()
    queue = _sort_edges(geom, ir)
    routed: dict[str, RoutePolyline] = {}
    ripup_count: dict[str, int] = defaultdict(int)
    overshoot: list[str] = []
    expansions_total = 0
    report = RouteOrchestratorReport(ripup_count=ripup_count, overshoot_edges=overshoot)

    rounds_used = 0
    for round_idx in range(cfg.max_rounds):
        rounds_used = round_idx + 1
        progressed = False
        pending = list(queue)
        queue = []
        for edge_id in pending:
            if edge_id in routed:
                continue
            res = _route_one(edge_id, geom, ir, artifact, routed, cfg)
            expansions_total += res.expansions
            if res.polyline is None:
                blockers = _find_blockers(edge_id, geom, routed)
                if blockers and ripup_count[edge_id] < cfg.max_ripup_per_edge:
                    ripup_count[edge_id] += 1
                    for b in blockers:
                        routed.pop(b, None)
                        queue.append(b)
                    queue.append(edge_id)
                    progressed = True
                else:
                    queue.append(edge_id)
                continue
            original = geom.routes[edge_id]
            routed[edge_id] = RoutePolyline(
                edge_id=edge_id,
                routing_class=original.routing_class,
                width=original.width,
                points=res.polyline,
            )
            if res.length_overshoot:
                overshoot.append(edge_id)
            progressed = True
        if not queue:
            break
        if not progressed:
            break

    new_routes = dict(geom.routes)
    for edge_id, poly in routed.items():
        new_routes[edge_id] = poly
    report.routed = list(routed)
    report.unrouted = [eid for eid in queue if eid not in routed]
    report.rounds_used = rounds_used
    report.expansions_total = expansions_total

    new_geom = GeometryIR(
        project=geom.project,
        board=geom.board,
        placements=geom.placements,
        routes=new_routes,
        nodes=geom.nodes,
        solve_status=geom.solve_status,
        solve_wall_seconds=geom.solve_wall_seconds,
        objective_value=geom.objective_value,
    )
    return new_geom, report


__all__ = [
    "DEFAULT_MAX_RIPUP_PER_EDGE",
    "DEFAULT_MAX_ROUNDS",
    "RouteOrchestratorConfig",
    "RouteOrchestratorReport",
    "route_all",
]
