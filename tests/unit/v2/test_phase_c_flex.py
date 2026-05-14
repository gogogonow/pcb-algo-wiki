"""Direct unit test for the orchestrator's Phase C helper.

The PA reference YAML has zero ``flexible_path`` edges, so this test
exercises ``_route_flex_edges`` with hand-built artifacts/IR/geometry to
verify the orchestrator wires up ``solver.astar_flex.route_flexible_paths``
correctly. We reuse the trivial fixtures from ``test_solver_astar_flex``
shape so coverage stays focused on the wiring, not on A* internals.
"""

from __future__ import annotations

from frontend.models import (
    BBox,
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
    TriagedEdge,
)
from schema.geometry_ir import GeometryIR
from schema.solver_ir import RoutingClass as SolverRoutingClass
from schema.solver_ir import SolverEdge, SolverIR
from schema.v6_ir import Board, Point, V6Terminal
from solver.v2.node_planner import NodePlan
from solver.v2.orchestrator import _route_flex_edges


def _trivial_artifact_with_flex() -> FrontendArtifact:
    pad_a = ExpandedPad(
        component="A", pin="P", abs_x=2.0, abs_y=10.0, orientation=0.0, kind="fixed"
    )
    pad_b = ExpandedPad(
        component="B", pin="P", abs_x=18.0, abs_y=10.0, orientation=0.0, kind="fixed"
    )
    return FrontendArtifact(
        project_name="flex_micro",
        board={"width": 20.0, "height": 20.0},
        lint_report=LintReport(),
        components={
            "A": ComponentExpansion(
                name="A",
                footprint_ref=None,
                placement_kind="fixed",
                pads=(pad_a,),
                bbox=BBox(min_x=1.5, min_y=9.5, max_x=2.5, max_y=10.5),
            ),
            "B": ComponentExpansion(
                name="B",
                footprint_ref=None,
                placement_kind="fixed",
                pads=(pad_b,),
                bbox=BBox(min_x=17.5, min_y=9.5, max_x=18.5, max_y=10.5),
            ),
        },
        fixed_terminals={"A.P": pad_a, "B.P": pad_b},
        edges={
            "flex1": TriagedEdge(
                name="flex1",
                edge_type="flexible_path",
                routing_class="flexible_path",
                target_length=None,
                width=0.3,
                connections=("A.P", "B.P"),
            )
        },
    )


def _trivial_ir() -> SolverIR:
    return SolverIR(
        project="flex_micro",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        clearance=0.15,
        terminals={
            "A.P": V6Terminal(point=Point(x=2.0, y=10.0)),
            "B.P": V6Terminal(point=Point(x=18.0, y=10.0)),
        },
        edges={
            "flex1": SolverEdge(
                endpoints=("A.P", "B.P"),
                routing_class=SolverRoutingClass.FLEXIBLE_PATH,
                width=0.3,
            )
        },
    )


def _empty_geom() -> GeometryIR:
    return GeometryIR(
        project="flex_micro",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        solve_status="FEASIBLE",
        solve_wall_seconds=0.0,
    )


def test_route_flex_edges_seeds_and_routes_flex() -> None:
    artifact = _trivial_artifact_with_flex()
    ir = _trivial_ir()
    geom = _empty_geom()
    plan = NodePlan(endpoint_xy={"A.P": (2.0, 10.0), "B.P": (18.0, 10.0)})

    new_geom, routed, failed = _route_flex_edges(
        ir=ir, artifact=artifact, plan=plan, geometry=geom, grid_step_um=500
    )

    assert "flex1" in routed
    assert failed == []
    assert "flex1" in new_geom.routes
    pts = new_geom.routes["flex1"].points
    assert len(pts) >= 2
    # Endpoints must remain pinned to the seed coordinates.
    assert (round(pts[0].x, 3), round(pts[0].y, 3)) == (2.0, 10.0)
    assert (round(pts[-1].x, 3), round(pts[-1].y, 3)) == (18.0, 10.0)


def test_route_flex_edges_no_op_when_no_flex() -> None:
    artifact = _trivial_artifact_with_flex()
    # Strip flex edge.
    object.__setattr__(artifact, "edges", {})
    new_geom, routed, failed = _route_flex_edges(
        ir=_trivial_ir(),
        artifact=artifact,
        plan=NodePlan(),
        geometry=_empty_geom(),
    )
    assert routed == []
    assert failed == []
    assert "flex1" not in new_geom.routes
