"""M5 — three-phase pipeline orchestrator.

Encapsulates the full ``YAML → SolverIR → SA seed → CP-SAT solve →
GeometryIR extract → A* flex routing → audit`` flow and exposes a single
entry point :func:`solve_layout`. Includes the M5 escalation policy: when
the CP-SAT solve returns INFEASIBLE / MODEL_INVALID we retry up to
``max_retries`` times with a hotter SA seed and a longer time budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from frontend.compile import compile_layout
from frontend.models import FrontendArtifact
from frontend.solver_ir import compile_solver_ir
from schema.geometry_ir import GeometryIR
from schema.solver_ir import SolverIR

from .astar_flex import AstarConfig, AstarReport, route_flexible_paths
from .audit import AuditReport, audit_geometry
from .cpsat import (
    DEFAULT_NUM_WORKERS,
    DEFAULT_TIME_LIMIT_S,
    SolveResult,
    build_model,
    solve_model,
)
from .extract import extract_geometry
from .route_orchestrator import (
    RouteOrchestratorConfig,
    RouteOrchestratorReport,
    route_all,
)
from .sa_floating import SaConfig, SaResult, hints_from_sa_result, run_sa


@dataclass
class OrchestratorOptions:
    use_sa: bool = True
    use_astar: bool = True
    use_octilinear: bool = True
    """M9: enable octilinear A* router + CP-SAT length-lock relaxation."""
    time_limit_s: float = DEFAULT_TIME_LIMIT_S
    num_workers: int = DEFAULT_NUM_WORKERS
    max_retries: int = 5
    sa_config: SaConfig | None = None
    astar_config: AstarConfig | None = None
    route_config: RouteOrchestratorConfig | None = None


@dataclass
class OrchestratorResult:
    artifact: FrontendArtifact
    ir: SolverIR
    geometry: GeometryIR
    audit: AuditReport
    solve: SolveResult
    sa: SaResult | None
    astar: AstarReport
    attempts: int
    skipped_locked_edges: list[tuple[str, str]] = field(default_factory=list)
    route: RouteOrchestratorReport | None = None
    """M9: octilinear router report (None when ``use_octilinear=False``)."""

    @property
    def status(self) -> str:
        return self.geometry.solve_status

    @property
    def wall_seconds_total(self) -> float:
        return float(self.geometry.solve_wall_seconds)


def _run_attempt(
    ir: SolverIR,
    artifact: FrontendArtifact,
    options: OrchestratorOptions,
    attempt: int,
) -> tuple[
    SaResult | None, SolveResult, GeometryIR, AuditReport, list[tuple[str, str]]
]:
    """One end-to-end pass: SA → CP-SAT → extract → audit (no A*)."""
    sa_result: SaResult | None = None
    hints = None
    if options.use_sa:
        cfg = options.sa_config or SaConfig()
        # Escalation: hotter initial temperature on each retry.
        if attempt > 0:
            cfg = SaConfig(
                iterations=cfg.iterations,
                temperature_init=cfg.temperature_init * (1.5**attempt),
                temperature_min=cfg.temperature_min,
                cooling=cfg.cooling,
                step_um=cfg.step_um,
                boundary_weight=cfg.boundary_weight,
                attract_weight=cfg.attract_weight,
                repulse_weight=cfg.repulse_weight,
                crossing_weight=cfg.crossing_weight,
                seed=(cfg.seed or 0) + attempt,
            )
        sa_result = run_sa(ir, artifact, cfg)
        hints = hints_from_sa_result(sa_result)

    cpsat = build_model(
        ir, artifact, seed_hints=hints, relax_length_lock=options.use_octilinear
    )
    time_limit = options.time_limit_s * (1.5**attempt)
    solve = solve_model(cpsat, time_limit_s=time_limit, num_workers=options.num_workers)
    geom = extract_geometry(ir=ir, artifact=artifact, cpsat=cpsat, result=solve)
    audit = audit_geometry(
        ir,
        geom,
        skip_length_edges={eid for eid, _ in cpsat.skipped_locked_edges},
        resolved_endpoints=cpsat.edge_endpoint_resolved,
    )
    return sa_result, solve, geom, audit, list(cpsat.skipped_locked_edges)


def solve_layout(
    yaml_path: str | Path,
    options: OrchestratorOptions | None = None,
) -> OrchestratorResult:
    """Run the full M5 pipeline on ``yaml_path`` and return the bundle."""
    options = options or OrchestratorOptions()
    artifact = compile_layout(str(yaml_path))
    ir = compile_solver_ir(str(yaml_path))

    sa_result: SaResult | None = None
    geom: GeometryIR | None = None
    audit: AuditReport | None = None
    solve: SolveResult | None = None
    skipped: list[tuple[str, str]] = []
    attempts = 0
    last_status = "UNKNOWN"

    for attempt in range(options.max_retries + 1):
        attempts = attempt + 1
        sa_result, solve, geom, audit, skipped = _run_attempt(
            ir, artifact, options, attempt
        )
        last_status = geom.solve_status
        if last_status in ("OPTIMAL", "FEASIBLE"):
            break

    assert geom is not None and solve is not None and audit is not None

    astar_report = AstarReport()
    route_report: RouteOrchestratorReport | None = None
    if options.use_octilinear and last_status in ("OPTIMAL", "FEASIBLE"):
        geom, route_report = route_all(
            ir=ir,
            artifact=artifact,
            geom=geom,
            config=options.route_config,
        )
        # Re-audit so length checks reflect the octilinear polylines.
        audit = audit_geometry(
            ir,
            geom,
            skip_length_edges={eid for eid, _ in skipped},
            resolved_endpoints=None,
        )
    if options.use_astar and last_status in ("OPTIMAL", "FEASIBLE"):
        geom, astar_report = route_flexible_paths(
            ir=ir,
            artifact=artifact,
            geom=geom,
            config=options.astar_config,
        )
        if astar_report.routed_edges:
            audit = audit_geometry(
                ir,
                geom,
                skip_length_edges={eid for eid, _ in skipped},
                resolved_endpoints=None,
            )

    return OrchestratorResult(
        artifact=artifact,
        ir=ir,
        geometry=geom,
        audit=audit,
        solve=solve,
        sa=sa_result,
        astar=astar_report,
        attempts=attempts,
        skipped_locked_edges=skipped,
        route=route_report,
    )


__all__ = [
    "OrchestratorOptions",
    "OrchestratorResult",
    "solve_layout",
]
