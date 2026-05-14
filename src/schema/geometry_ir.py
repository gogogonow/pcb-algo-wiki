"""Strict pydantic models for the M4 GeometryIR (post-solve geometry).

This is the "post-solve" sibling of ``solver_ir.SolverIR``: it carries the
concrete (x, y) coordinates that the CP-SAT solver produced for every
endpoint, plus per-edge polyline geometry suitable for SVG rendering and
length / no-overlap regression assertions.

GeometryIR keeps everything in floating-point millimetres (consistent with
v3.3 / v6 IR) — the µm discretisation is an implementation detail of the
``solver`` package and is converted back at extraction time.
"""

from __future__ import annotations

from typing import Self

from pydantic import (
    Field,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .v6_ir import (
    Board,
    ExactFloat,
    Point,
    RoutingClass,
    StrictFrozenModel,
    require_positive_float,
)


class PinPlacement(StrictFrozenModel):
    pin: StrictStr
    point: Point


class ComponentPlacement(StrictFrozenModel):
    component: StrictStr
    anchor: Point
    rotation_deg: ExactFloat
    pads: tuple[PinPlacement, ...]

    @field_validator("rotation_deg")
    @classmethod
    def validate_rotation(cls, value: float) -> float:
        if value not in (0.0, 90.0, 180.0, 270.0, -90.0):
            raise ValueError(
                "rotation_deg must be one of 0/90/180/270/-90 degrees in M4"
            )
        return value


class RoutePolyline(StrictFrozenModel):
    edge_id: StrictStr
    routing_class: RoutingClass
    width: ExactFloat
    points: tuple[Point, ...]

    @field_validator("width")
    @classmethod
    def validate_width(cls, value: float, info: ValidationInfo) -> float:
        return require_positive_float(value, info.field_name)

    @field_validator("points")
    @classmethod
    def validate_points(cls, value: tuple[Point, ...]) -> tuple[Point, ...]:
        if len(value) < 2:
            raise ValueError("RoutePolyline requires at least 2 points")
        return value


class GeometryIR(StrictFrozenModel):
    project: StrictStr
    board: Board
    placements: dict[str, ComponentPlacement] = Field(default_factory=dict)
    routes: dict[str, RoutePolyline] = Field(default_factory=dict)
    nodes: dict[str, Point] = Field(default_factory=dict)
    solve_status: StrictStr
    solve_wall_seconds: ExactFloat
    objective_value: ExactFloat | None = None

    @field_validator("solve_wall_seconds")
    @classmethod
    def validate_wall_seconds(cls, value: float) -> float:
        if value < 0.0:
            raise ValueError("solve_wall_seconds must be ≥ 0")
        return value

    @model_validator(mode="after")
    def validate_solve_status(self) -> Self:
        allowed = {"OPTIMAL", "FEASIBLE", "INFEASIBLE", "MODEL_INVALID", "UNKNOWN"}
        if self.solve_status not in allowed:
            raise ValueError(
                f"solve_status must be one of {sorted(allowed)}, "
                f"got {self.solve_status!r}"
            )
        return self


__all__ = [
    "ComponentPlacement",
    "GeometryIR",
    "PinPlacement",
    "RoutePolyline",
]
