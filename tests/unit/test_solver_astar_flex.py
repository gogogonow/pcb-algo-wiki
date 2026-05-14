"""Unit tests for ``solver.astar_flex`` (M5 Phase 3 routing)."""

from __future__ import annotations

from frontend.models import (
    BBox,
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
)
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point, V6Terminal
from solver.astar_flex import AstarConfig, route_flexible_paths


def _trivial_artifact() -> FrontendArtifact:
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
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.3,
            )
        },
    )


def _seed_geom_with_obstacle() -> GeometryIR:
    """Geometry with a vertical RF route forming an obstacle in the middle."""
    return GeometryIR(
        project="flex_micro",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
        routes={
            "flex1": RoutePolyline(
                edge_id="flex1",
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.3,
                points=(Point(x=2.0, y=10.0), Point(x=18.0, y=10.0)),
            ),
            "rf_obs": RoutePolyline(
                edge_id="rf_obs",
                routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
                width=2.0,
                points=(Point(x=10.0, y=4.0), Point(x=10.0, y=16.0)),
            ),
        },
    )


def test_route_flexible_paths_no_op_when_no_flexible_edges() -> None:
    ir = _trivial_ir()
    # Replace the only flex edge with a locked one to simulate "no flex"
    edges = {
        "locked": SolverEdge(
            endpoints=("A.P", "B.P"),
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            target_length=16.0,
            width=0.3,
        )
    }
    ir2 = SolverIR(
        project=ir.project,
        board=ir.board,
        clearance=ir.clearance,
        terminals=ir.terminals,
        edges=edges,
    )
    geom = GeometryIR(
        project="flex_micro",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
    )
    new_geom, report = route_flexible_paths(
        ir=ir2, artifact=_trivial_artifact(), geom=geom
    )
    assert new_geom is geom
    assert not report.routed_edges and not report.failed_edges


def test_route_flexible_paths_detours_around_rf_obstacle() -> None:
    ir = _trivial_ir()
    artifact = _trivial_artifact()
    geom = _seed_geom_with_obstacle()
    new_geom, report = route_flexible_paths(
        ir=ir, artifact=artifact, geom=geom, config=AstarConfig(grid_step_um=500)
    )
    assert "flex1" in report.routed_edges
    assert "flex1" not in report.failed_edges
    poly = new_geom.routes["flex1"]
    # Original polyline had 2 points; A* polyline must have at least 3 (a detour).
    assert len(poly.points) >= 3
    # Endpoint snapping preserved.
    assert (poly.points[0].x, poly.points[0].y) == (2.0, 10.0)
    assert (poly.points[-1].x, poly.points[-1].y) == (18.0, 10.0)
    # Detour must clear x=10 corridor (must dip below y=4 or above y=16
    # after RF inflate). Confirm at least one waypoint is outside [4,16] in y.
    inner = poly.points[1:-1]
    assert any(p.y < 4.0 or p.y > 16.0 for p in inner)
