"""M6 soft LVS tests."""

from __future__ import annotations

from postproc.lvs import run_lvs
from schema.geometry_ir import GeometryIR
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=10.0, height=10.0)


def _ir(edges: dict[str, SolverEdge]) -> SolverIR:
    return SolverIR(project="t", board=_board(), clearance=0.15, edges=edges)


def _geom() -> GeometryIR:
    return GeometryIR(
        project="t",
        board=_board(),
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
    )


def _e(eps: tuple[str, str]) -> SolverEdge:
    return SolverEdge(endpoints=eps, routing_class=RoutingClass.FLEXIBLE_PATH)


def test_skipped_when_no_logical_net_provided() -> None:
    ir = _ir({"e1": _e(("a", "b"))})
    rep = run_lvs(ir, _geom())
    assert rep.skipped
    assert rep.passed
    assert "no logical_net" in rep.reason


def test_single_connected_logical_net_passes() -> None:
    ir = _ir(
        {
            "e1": _e(("a", "b")),
            "e2": _e(("b", "c")),
        }
    )
    rep = run_lvs(ir, _geom(), logical_net_of_edge={"e1": "VCC", "e2": "VCC"})
    assert not rep.skipped
    assert rep.passed
    assert rep.checked_nets == ("VCC",)


def test_disconnected_logical_net_is_flagged() -> None:
    ir = _ir(
        {
            "e1": _e(("a", "b")),
            "e2": _e(("c", "d")),
        }
    )
    rep = run_lvs(ir, _geom(), logical_net_of_edge={"e1": "VCC", "e2": "VCC"})
    assert not rep.passed
    assert len(rep.mismatches) == 1
    assert rep.mismatches[0].logical_net == "VCC"
    assert rep.mismatches[0].component_count == 2
