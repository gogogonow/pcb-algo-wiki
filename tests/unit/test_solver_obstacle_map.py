"""Unit tests for ``solver.obstacle_map`` (M9 obstacle grid)."""

from __future__ import annotations

from frontend.models import (
    BBox,
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
)
from schema.geometry_ir import RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point, V6Terminal
from solver.obstacle_map import (
    DEFAULT_GRID_STEP_UM,
    DEFAULT_PAD_HALO_UM,
    build_obstacle_map,
)


def _ir() -> SolverIR:
    return SolverIR(
        project="m9_obs",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0),
        clearance=0.15,
        terminals={
            "A.P": V6Terminal(point=Point(x=2.0, y=10.0)),
            "B.P": V6Terminal(point=Point(x=18.0, y=10.0)),
        },
        edges={
            "e1": SolverEdge(
                endpoints=("A.P", "B.P"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.3,
            )
        },
    )


def _artifact() -> FrontendArtifact:
    pad_a = ExpandedPad(
        component="A", pin="P", abs_x=2.0, abs_y=10.0, orientation=0.0, kind="fixed"
    )
    pad_b = ExpandedPad(
        component="B", pin="P", abs_x=18.0, abs_y=10.0, orientation=0.0, kind="fixed"
    )
    return FrontendArtifact(
        project_name="m9_obs",
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


def test_build_obstacle_map_marks_component_bboxes() -> None:
    om = build_obstacle_map(ir=_ir(), artifact=_artifact(), step_um=500)
    # Component A (1.5..2.5, 9.5..10.5) inflated by clearance 0.15mm → cells
    # around (cx≈4, cy≈20) at 500µm step must be blocked.
    assert (4, 20) in om.obstacles
    assert om.cell_count() > 0


def test_build_obstacle_map_routed_polyline_inflates_along_segment() -> None:
    routed = {
        "rf1": RoutePolyline(
            edge_id="rf1",
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            width=2.0,
            points=(Point(x=10.0, y=4.0), Point(x=10.0, y=16.0)),
        )
    }
    om = build_obstacle_map(
        ir=_ir(),
        artifact=_artifact(),
        routed=routed,
        step_um=500,
    )
    # The RF route at x=10mm is 2mm wide → inflated half-width 1mm + 0.15mm
    # clearance → cells in x∈[18..21] (mm/.5) blocked across most of y.
    blocked_in_corridor = sum(1 for c in om.obstacles if c[0] == 20 and 8 <= c[1] <= 32)
    assert blocked_in_corridor >= 5


def test_build_obstacle_map_extra_inflate_grows_obstacles() -> None:
    base = build_obstacle_map(
        ir=_ir(),
        artifact=_artifact(),
        step_um=DEFAULT_GRID_STEP_UM,
        pad_halo_um=DEFAULT_PAD_HALO_UM,
    )
    grown = build_obstacle_map(
        ir=_ir(),
        artifact=_artifact(),
        step_um=DEFAULT_GRID_STEP_UM,
        pad_halo_um=DEFAULT_PAD_HALO_UM,
        extra_inflate_um=1000,
    )
    assert grown.cell_count() > base.cell_count()


def test_obstacle_map_grid_bounds_contains_only_in_board() -> None:
    om = build_obstacle_map(ir=_ir(), artifact=_artifact(), step_um=500)
    assert om.bounds.contains((0, 0))
    assert om.bounds.contains((40, 40))  # 20mm/0.5mm step = 40
    assert not om.bounds.contains((-1, 0))
    assert not om.bounds.contains((41, 0))
