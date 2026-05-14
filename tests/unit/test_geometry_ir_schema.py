"""Strict-schema tests for ``schema.geometry_ir.GeometryIR``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schema.geometry_ir import (
    ComponentPlacement,
    GeometryIR,
    PinPlacement,
    RoutePolyline,
)
from schema.solver_ir import RoutingClass
from schema.v6_ir import Board, Point


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=40.0, height=100.0)


def _placement() -> ComponentPlacement:
    return ComponentPlacement(
        component="C1",
        anchor=Point(x=1.0, y=2.0),
        rotation_deg=0.0,
        pads=(PinPlacement(pin="PIN_1", point=Point(x=1.0, y=2.0)),),
    )


def _route() -> RoutePolyline:
    return RoutePolyline(
        edge_id="e1",
        routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
        width=0.5,
        points=(Point(x=0.0, y=0.0), Point(x=1.0, y=0.0)),
    )


def test_valid_geometry_ir_round_trips() -> None:
    geom = GeometryIR(
        project="demo",
        board=_board(),
        placements={"C1": _placement()},
        routes={"e1": _route()},
        nodes={"n1": Point(x=5.0, y=5.0)},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.123,
        objective_value=None,
    )
    assert geom.solve_status == "OPTIMAL"
    assert geom.routes["e1"].points[1].x == 1.0


def test_rotation_must_be_orthogonal() -> None:
    with pytest.raises(ValidationError):
        ComponentPlacement(
            component="C1",
            anchor=Point(x=0.0, y=0.0),
            rotation_deg=15.0,
            pads=(),
        )


def test_route_requires_at_least_two_points() -> None:
    with pytest.raises(ValidationError):
        RoutePolyline(
            edge_id="e1",
            routing_class=RoutingClass.RF_CONSTRAINED_FREE,
            width=0.5,
            points=(Point(x=0.0, y=0.0),),
        )


def test_solve_status_restricted() -> None:
    with pytest.raises(ValidationError):
        GeometryIR(
            project="demo",
            board=_board(),
            placements={},
            routes={},
            nodes={},
            solve_status="MAYBE",
            solve_wall_seconds=0.0,
            objective_value=None,
        )


def test_wall_seconds_non_negative() -> None:
    with pytest.raises(ValidationError):
        GeometryIR(
            project="demo",
            board=_board(),
            placements={},
            routes={},
            nodes={},
            solve_status="OPTIMAL",
            solve_wall_seconds=-1.0,
            objective_value=None,
        )
