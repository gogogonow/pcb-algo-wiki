"""M6 DRC tests."""

from __future__ import annotations

from postproc.drc import run_drc
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=50.0, height=50.0)


def _r(eid: str, width: float, pts: tuple[Point, ...]) -> RoutePolyline:
    return RoutePolyline(
        edge_id=eid,
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=width,
        points=pts,
    )


def _e(net: str | None = None, width: float | None = 0.254) -> SolverEdge:
    return SolverEdge(
        endpoints=("a", "b"),
        routing_class=RoutingClass.FLEXIBLE_PATH,
        net=net,
        width=width,
    )


def _ir(edges: dict[str, SolverEdge], clearance: float = 0.15) -> SolverIR:
    return SolverIR(project="t", board=_board(), clearance=clearance, edges=edges)


def _geom(routes: dict[str, RoutePolyline]) -> GeometryIR:
    return GeometryIR(
        project="t",
        board=_board(),
        routes=routes,
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
    )


def test_clean_layout_passes() -> None:
    ir = _ir(
        {
            "e1": SolverEdge(
                endpoints=("a", "b"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.254,
            ),
            "e2": SolverEdge(
                endpoints=("c", "d"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.254,
            ),
        }
    )
    geom = _geom(
        {
            "e1": _r("e1", 0.254, (Point(x=0.0, y=0.0), Point(x=10.0, y=0.0))),
            "e2": _r("e2", 0.254, (Point(x=0.0, y=20.0), Point(x=10.0, y=20.0))),
        }
    )
    rep = run_drc(geom, ir)
    assert rep.passed
    assert rep.critical_count == 0


def test_min_width_violation_is_critical() -> None:
    ir = _ir({"e1": _e(width=0.254)})
    geom = _geom({"e1": _r("e1", 0.1, (Point(x=0.0, y=0.0), Point(x=10.0, y=0.0)))})
    rep = run_drc(geom, ir, min_trace_width=0.254)
    assert rep.critical_count == 1
    assert rep.violations[0].rule == "min_width"


def test_clearance_violation_between_unrelated_nets() -> None:
    ir = _ir(
        {
            "e1": SolverEdge(
                endpoints=("a", "b"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                net="N1",
                width=0.254,
            ),
            "e2": SolverEdge(
                endpoints=("c", "d"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                net="N2",
                width=0.254,
            ),
        },
        clearance=0.5,
    )
    geom = _geom(
        {
            "e1": _r("e1", 0.254, (Point(x=0.0, y=0.0), Point(x=10.0, y=0.0))),
            "e2": _r("e2", 0.254, (Point(x=0.0, y=0.3), Point(x=10.0, y=0.3))),
        }
    )
    rep = run_drc(geom, ir)
    crit = [v for v in rep.violations if v.rule == "min_clearance"]
    assert len(crit) == 1
    assert crit[0].severity == "critical"


def test_same_net_pair_does_not_violate_clearance() -> None:
    ir = _ir(
        {
            "e1": SolverEdge(
                endpoints=("a", "b"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                net="N1",
                width=0.254,
            ),
            "e2": SolverEdge(
                endpoints=("c", "d"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                net="N1",
                width=0.254,
            ),
        },
        clearance=0.5,
    )
    geom = _geom(
        {
            "e1": _r("e1", 0.254, (Point(x=0.0, y=0.0), Point(x=10.0, y=0.0))),
            "e2": _r("e2", 0.254, (Point(x=0.0, y=0.3), Point(x=10.0, y=0.3))),
        }
    )
    rep = run_drc(geom, ir)
    assert rep.critical_count == 0


def test_shared_endpoint_pair_skipped() -> None:
    ir = _ir(
        {
            "e1": SolverEdge(
                endpoints=("X", "b"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.254,
            ),
            "e2": SolverEdge(
                endpoints=("X", "d"),
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.254,
            ),
        },
        clearance=0.5,
    )
    geom = _geom(
        {
            "e1": _r("e1", 0.254, (Point(x=0.0, y=0.0), Point(x=10.0, y=0.0))),
            "e2": _r("e2", 0.254, (Point(x=0.0, y=0.3), Point(x=10.0, y=0.3))),
        }
    )
    rep = run_drc(geom, ir)
    assert rep.critical_count == 0


def test_explicit_overrides_take_precedence() -> None:
    ir = _ir({"e1": _e(width=0.254)})
    geom = _geom({"e1": _r("e1", 0.254, (Point(x=0.0, y=0.0), Point(x=10.0, y=0.0)))})
    rep = run_drc(geom, ir, min_trace_width=0.5)
    assert rep.critical_count == 1
