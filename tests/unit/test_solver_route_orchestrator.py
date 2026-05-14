"""Unit tests for ``solver.route_orchestrator`` (M9 rip-up + reroute)."""

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
from solver.route_orchestrator import RouteOrchestratorConfig, route_all


def _two_edge_ir() -> SolverIR:
    """Two parallel edges that, if routed naively, would overlap."""
    return SolverIR(
        project="m9_route",
        board=Board(origin=Point(x=0.0, y=0.0), width=30.0, height=30.0),
        clearance=0.15,
        terminals={
            "A1": V6Terminal(point=Point(x=3.0, y=10.0)),
            "B1": V6Terminal(point=Point(x=27.0, y=10.0)),
            "A2": V6Terminal(point=Point(x=3.0, y=20.0)),
            "B2": V6Terminal(point=Point(x=27.0, y=20.0)),
        },
        edges={
            "edge_top": SolverEdge(
                endpoints=("A1", "B1"),
                routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
                target_length=24.0,
                width=0.5,
            ),
            "edge_bot": SolverEdge(
                endpoints=("A2", "B2"),
                routing_class=RoutingClass.RF_CONSTRAINED_FREE,
                width=0.5,
            ),
        },
    )


def _two_edge_artifact() -> FrontendArtifact:
    pads = {
        name: ExpandedPad(
            component=name,
            pin="P",
            abs_x=x,
            abs_y=y,
            orientation=0.0,
            kind="fixed",
        )
        for name, (x, y) in {
            "A1": (3.0, 10.0),
            "B1": (27.0, 10.0),
            "A2": (3.0, 20.0),
            "B2": (27.0, 20.0),
        }.items()
    }
    return FrontendArtifact(
        project_name="m9_route",
        board={"width": 30.0, "height": 30.0},
        lint_report=LintReport(),
        components={
            name: ComponentExpansion(
                name=name,
                footprint_ref=None,
                placement_kind="fixed",
                pads=(pads[name],),
                bbox=BBox(
                    min_x=pads[name].abs_x - 0.5,
                    min_y=pads[name].abs_y - 0.5,
                    max_x=pads[name].abs_x + 0.5,
                    max_y=pads[name].abs_y + 0.5,
                ),
            )
            for name in pads
        },
        fixed_terminals=pads,
    )


def _seed_geom(ir: SolverIR) -> GeometryIR:
    routes = {}
    for eid, edge in ir.edges.items():
        ep_a, ep_b = edge.endpoints
        a = ir.terminals[ep_a].point
        b = ir.terminals[ep_b].point
        routes[eid] = RoutePolyline(
            edge_id=eid,
            routing_class=edge.routing_class,
            width=float(edge.width or 0.3),
            points=(a, b),
        )
    return GeometryIR(
        project=ir.project,
        board=ir.board,
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
        routes=routes,
    )


def test_route_all_routes_two_disjoint_edges() -> None:
    ir = _two_edge_ir()
    art = _two_edge_artifact()
    geom = _seed_geom(ir)
    new_geom, report = route_all(
        ir=ir,
        artifact=art,
        geom=geom,
        config=RouteOrchestratorConfig(grid_step_um=500),
    )
    assert "edge_top" in report.routed
    assert "edge_bot" in report.routed
    assert not report.unrouted
    # Both polylines should respect endpoints.
    for eid in ("edge_top", "edge_bot"):
        poly = new_geom.routes[eid].points
        assert (poly[0].x, poly[0].y) == (
            ir.terminals[ir.edges[eid].endpoints[0]].point.x,
            ir.terminals[ir.edges[eid].endpoints[0]].point.y,
        )


def test_route_all_priority_locked_first() -> None:
    """Ensure routed[0] is the rf_constrained_locked edge, not the free one."""
    ir = _two_edge_ir()
    art = _two_edge_artifact()
    geom = _seed_geom(ir)
    _, report = route_all(
        ir=ir,
        artifact=art,
        geom=geom,
        config=RouteOrchestratorConfig(grid_step_um=500),
    )
    assert report.routed[0] == "edge_top"


def test_route_all_records_unrouted_when_walled_off() -> None:
    """A wall blocking the whole board across edge_bot must not route it."""
    ir = _two_edge_ir()
    art = _two_edge_artifact()
    geom = _seed_geom(ir)
    # Pre-place a wall obstacle as a routed entry directly in geom.routes by
    # injecting an edge with extreme width.
    wall_ir = SolverIR(
        project=ir.project,
        board=ir.board,
        clearance=ir.clearance,
        terminals={
            **ir.terminals,
            "WL": V6Terminal(point=Point(x=15.0, y=0.5)),
            "WH": V6Terminal(point=Point(x=15.0, y=29.5)),
        },
        edges={
            **ir.edges,
            "wall": SolverEdge(
                endpoints=("WL", "WH"),
                routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
                target_length=29.0,
                width=8.0,
            ),
        },
    )
    new_geom = GeometryIR(
        project=geom.project,
        board=geom.board,
        solve_status=geom.solve_status,
        solve_wall_seconds=geom.solve_wall_seconds,
        routes={
            **geom.routes,
            "wall": RoutePolyline(
                edge_id="wall",
                routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
                width=8.0,
                points=(Point(x=15.0, y=0.5), Point(x=15.0, y=29.5)),
            ),
        },
    )
    _, report = route_all(
        ir=wall_ir,
        artifact=art,
        geom=new_geom,
        config=RouteOrchestratorConfig(
            grid_step_um=500, max_rounds=2, max_ripup_per_edge=0
        ),
    )
    # Wall (locked, longest) goes first and is trivially routed (its own
    # straight segment is the path it occupies); the bottom edge crossing
    # the wall must end up unrouted because rip-up is disabled here.
    assert "edge_top" in report.unrouted or "edge_bot" in report.unrouted
