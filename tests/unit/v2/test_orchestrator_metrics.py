from __future__ import annotations

from frontend.models import FrontendArtifact, LintReport
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.v6_ir import Board, Point, RoutingClass
from solver.v2.orchestrator import (
    OrchestratorV2Result,
    PhaseAResult,
    PhaseBResult,
    PhaseCResult,
    phase_summary,
)
from solver.v2.node_planner import NodePlan
from solver.v2.skeleton_router import SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport


def test_phase_summary_contains_quality_metrics() -> None:
    artifact = FrontendArtifact(
        project_name="demo",
        board={"width": 20.0, "height": 20.0},
        lint_report=LintReport(),
    )
    route = RoutePolyline(
        edge_id="e1",
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=0.2,
        points=(
            Point(x=0.0, y=0.0),
            Point(x=3.0, y=4.0),
            Point(x=6.0, y=4.0),
        ),
    )
    geometry = GeometryIR(
        project="demo",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        routes={"e1": route},
        solve_status="FEASIBLE",
        solve_wall_seconds=1.23,
    )
    result = OrchestratorV2Result(
        artifact=artifact,
        phase_a=PhaseAResult(skeleton=SkeletonReport(), plan=NodePlan(), wall_seconds=0.1),
        phase_b=PhaseBResult(adhesion=UvAdhesionReport(), wall_seconds=0.2),
        phase_c=PhaseCResult(
            routed_flex_edges=["e1"],
            failed_flex_edges=[],
            wall_seconds=0.3,
            drc_violations=2,
        ),
        geometry=geometry,
    )
    summary = phase_summary(result)

    assert summary["phase_c"]["drc_violations"] == 2
    assert summary["route_total_segments"] == 2
    assert summary["route_total_length_mm"] == 8.0
