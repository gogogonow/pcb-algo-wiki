"""TDD for M11-B: retry failed Phase A routes after Phase B."""

from __future__ import annotations

from frontend.models import FrontendArtifact, LintReport, TriagedEdge
from schema.geometry_ir import GeometryIR
from schema.v6_ir import Board, Point
from solver.v2.node_planner import NodePlan
from solver.v2.orchestrator import OrchestratorV2Options, solve_layout_v2
from solver.v2.skeleton_router import RouteOutcome, SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport


def _artifact() -> FrontendArtifact:
    return FrontendArtifact(
        project_name="retry_case",
        board={"width": 20.0, "height": 20.0},
        lint_report=LintReport(),
        edges={
            "edge_fail": TriagedEdge(
                name="edge_fail",
                edge_type="microstrip",
                routing_class="rf_constrained_locked",
                target_length=6.0,
                width=1.0,
                connections=("A.P", "B.P"),
            ),
            "edge_ok": TriagedEdge(
                name="edge_ok",
                edge_type="microstrip",
                routing_class="rf_constrained_locked",
                target_length=5.0,
                width=0.8,
                connections=("C.P", "D.P"),
            ),
        },
    )


def _geom() -> GeometryIR:
    return GeometryIR(
        project="retry_case",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        solve_status="FEASIBLE",
        solve_wall_seconds=0.0,
    )


def test_solve_layout_v2_retries_failed_phase_a_edges(monkeypatch) -> None:
    artifact = _artifact()
    plan = NodePlan()
    first = SkeletonReport(
        routes={
            "edge_fail": RouteOutcome(
                edge_id="edge_fail",
                polyline_um=(),
                length_mm=0.0,
                target_mm=6.0,
                success=False,
                rip_up_round=0,
                failure_reason="no path",
            ),
            "edge_ok": RouteOutcome(
                edge_id="edge_ok",
                polyline_um=((1000, 1000), (8000, 1000)),
                length_mm=7.0,
                target_mm=5.0,
                success=True,
                rip_up_round=0,
            ),
        },
        final_endpoint_um={"C.P": (1000, 1000), "D.P": (8000, 1000)},
        rip_up_rounds=0,
    )
    second = SkeletonReport(
        routes={
            "edge_fail": RouteOutcome(
                edge_id="edge_fail",
                polyline_um=((2000, 3000), (9000, 3000)),
                length_mm=7.0,
                target_mm=6.0,
                success=True,
                rip_up_round=1,
            )
        },
        final_endpoint_um={"A.P": (2000, 3000), "B.P": (9000, 3000)},
        rip_up_rounds=1,
    )
    route_calls: list[tuple[float, int, int, int]] = []

    def fake_route_skeleton(
        _artifact, _plan, *, clearance_mm, grid_config, rip_up_rounds
    ):
        route_calls.append(
            (
                float(clearance_mm),
                int(grid_config.step_um),
                int(grid_config.max_expansions),
                int(rip_up_rounds),
            )
        )
        return first if len(route_calls) == 1 else second

    monkeypatch.setattr("solver.v2.orchestrator.compile_layout", lambda _p: artifact)
    monkeypatch.setattr(
        "solver.v2.orchestrator.plan_node_positions",
        lambda *_a, **_k: plan,
    )
    monkeypatch.setattr("solver.v2.orchestrator.route_skeleton", fake_route_skeleton)
    monkeypatch.setattr(
        "solver.v2.orchestrator.adhere_uv_components",
        lambda *_a, **_k: UvAdhesionReport(),
    )
    monkeypatch.setattr(
        "solver.v2.orchestrator._assemble_geometry", lambda **_k: _geom()
    )

    def _raise_compile_solver_ir(*_a, **_k):
        raise RuntimeError("skip flex in retry unit test")

    monkeypatch.setattr(
        "solver.v2.orchestrator.compile_solver_ir", _raise_compile_solver_ir
    )

    result = solve_layout_v2(
        "dummy.yaml",
        options=OrchestratorV2Options(
            clearance_mm=0.2, grid_step_um=100, rip_up_rounds=3
        ),
    )

    assert len(route_calls) == 2
    assert route_calls[0] == (0.2, 100, 40000, 3)
    assert route_calls[1] == (0.1, 200, 200000, 3)
    assert result.phase_a.skeleton.routes["edge_fail"].success is True
    assert result.phase_a.skeleton.routes["edge_ok"].success is True
