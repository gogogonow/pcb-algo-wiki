"""Unit tests for ``solver.astar_octilinear`` (M9 8-direction A* router)."""

from __future__ import annotations

import math

from frontend.models import (
    ExpandedPad,
    FrontendArtifact,
    LintReport,
)
from schema.geometry_ir import RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point, V6Terminal
from solver.astar_octilinear import OctilinearConfig, search
from solver.obstacle_map import build_obstacle_map


def _ir() -> SolverIR:
    return SolverIR(
        project="m9_oct",
        board=Board(origin=Point(x=0.0, y=0.0), width=30.0, height=30.0),
        clearance=0.15,
        terminals={
            "A": V6Terminal(point=Point(x=3.0, y=15.0)),
            "B": V6Terminal(point=Point(x=27.0, y=15.0)),
        },
        edges={
            "e1": SolverEdge(
                endpoints=("A", "B"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.3,
            )
        },
    )


def _artifact() -> FrontendArtifact:
    return FrontendArtifact(
        project_name="m9_oct",
        board={"width": 30.0, "height": 30.0},
        lint_report=LintReport(),
        components={},
        fixed_terminals={
            "A": ExpandedPad(
                component="A",
                pin="P",
                abs_x=3.0,
                abs_y=15.0,
                orientation=0.0,
                kind="fixed",
            ),
            "B": ExpandedPad(
                component="B",
                pin="P",
                abs_x=27.0,
                abs_y=15.0,
                orientation=0.0,
                kind="fixed",
            ),
        },
    )


def test_octilinear_finds_straight_line_when_unobstructed() -> None:
    om = build_obstacle_map(ir=_ir(), artifact=_artifact(), step_um=500)
    res = search(
        start_xy_mm=(3.0, 15.0),
        goal_xy_mm=(27.0, 15.0),
        obstacles=om,
    )
    assert res.polyline is not None
    # Endpoints preserved.
    assert (res.polyline[0].x, res.polyline[0].y) == (3.0, 15.0)
    assert (res.polyline[-1].x, res.polyline[-1].y) == (27.0, 15.0)
    # Length ≈ 24 mm (24000 µm) within one grid step.
    assert abs(res.length_um - 24000) < 600


def test_octilinear_uses_45_degree_when_obstacle_blocks_axial() -> None:
    ir = _ir()
    art = _artifact()
    blocker = {
        "rf_block": RoutePolyline(
            edge_id="rf_block",
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            width=2.0,
            points=(Point(x=15.0, y=12.0), Point(x=15.0, y=18.0)),
        )
    }
    om = build_obstacle_map(ir=ir, artifact=art, routed=blocker, step_um=500)
    res = search(
        start_xy_mm=(3.0, 15.0),
        goal_xy_mm=(27.0, 15.0),
        obstacles=om,
        config=OctilinearConfig(turn_cost_45=0.05, turn_cost_90=2.0),
    )
    assert res.polyline is not None
    # Detour must include at least one diagonal segment (Δx ≈ Δy ≠ 0).
    has_diag = False
    for i in range(len(res.polyline) - 1):
        dx = abs(res.polyline[i + 1].x - res.polyline[i].x)
        dy = abs(res.polyline[i + 1].y - res.polyline[i].y)
        if dx > 0.05 and dy > 0.05 and abs(dx - dy) < 0.1:
            has_diag = True
            break
    assert has_diag, "expected a 45° (diagonal) segment in the detour polyline"


def test_octilinear_returns_none_when_completely_walled_off() -> None:
    ir = _ir()
    art = _artifact()
    # Wall completely across the board — much wider than the endpoint halo.
    wall = {
        "wall": RoutePolyline(
            edge_id="wall",
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            width=8.0,
            points=(Point(x=15.0, y=0.5), Point(x=15.0, y=29.5)),
        )
    }
    om = build_obstacle_map(ir=ir, artifact=art, routed=wall, step_um=500)
    res = search(
        start_xy_mm=(3.0, 15.0),
        goal_xy_mm=(27.0, 15.0),
        obstacles=om,
        config=OctilinearConfig(endpoint_halo_cells=1),
    )
    assert res.polyline is None


def test_octilinear_overshoot_flag_when_path_exceeds_target() -> None:
    om = build_obstacle_map(ir=_ir(), artifact=_artifact(), step_um=500)
    res = search(
        start_xy_mm=(3.0, 15.0),
        goal_xy_mm=(27.0, 15.0),
        obstacles=om,
        target_length_mm=10.0,  # well below the actual ≈24mm
        length_tol_um=500,
    )
    assert res.polyline is not None
    assert res.length_overshoot is True


def test_octilinear_no_overshoot_when_path_below_target() -> None:
    om = build_obstacle_map(ir=_ir(), artifact=_artifact(), step_um=500)
    res = search(
        start_xy_mm=(3.0, 15.0),
        goal_xy_mm=(27.0, 15.0),
        obstacles=om,
        target_length_mm=40.0,
        length_tol_um=500,
    )
    assert res.polyline is not None
    assert res.length_overshoot is False
    # Sanity: shortest polyline length ≈ 24 mm, comfortably below target.
    assert res.length_um < math.ceil(40_000)


def test_start_dir_respected() -> None:
    """A* respects start_dir for the first few steps."""
    from solver.obstacle_map import GridBounds, ObstacleMap

    bounds = GridBounds(0, 0, 200, 200)
    omap = ObstacleMap(bounds=bounds, obstacles=set(), step_um=1_000)

    # Start (5,5) → goal (15,5) in mm, start_dir=(+1,0)=right
    result = search(
        start_xy_mm=(5.0, 5.0),
        goal_xy_mm=(15.0, 5.0),
        obstacles=omap,
        config=OctilinearConfig(dir_lock_cells=3, dir_lock_penalty=10.0),
        start_dir=(1, 0),
        end_dir=(-1, 0),
    )
    assert result.polyline is not None
    pts = result.polyline
    assert pts[1].x > pts[0].x, "first step must move in +X direction"
    assert abs(pts[1].y - pts[0].y) < 0.01, "first step must be horizontal"


def test_dir_lock_cells_defaults_to_nonnegative() -> None:
    """OctilinearConfig must have dir_lock_cells with a non-negative default."""
    cfg = OctilinearConfig()
    assert hasattr(cfg, "dir_lock_cells")
    assert cfg.dir_lock_cells >= 0
