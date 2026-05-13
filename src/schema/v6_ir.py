"""Strict Pydantic models for the v6 IR schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    StrictStr,
    model_validator,
)


def require_exact_float(value: Any) -> Any:
    if type(value) is not float:
        raise ValueError("value must be a float")
    return value


def require_exact_tuple(value: Any) -> Any:
    if type(value) is not tuple:
        raise ValueError("value must be a tuple")
    return value


ExactFloat = Annotated[StrictFloat, BeforeValidator(require_exact_float)]
EndpointTuple = Annotated[
    tuple[StrictStr, StrictStr],
    BeforeValidator(require_exact_tuple),
]


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoutingClass(StrEnum):
    RF_CONSTRAINED_LOCKED = "rf_constrained_locked"
    RF_CONSTRAINED_FREE = "rf_constrained_free"
    FLEXIBLE_PATH = "flexible_path"


class Point(StrictFrozenModel):
    x: ExactFloat
    y: ExactFloat


class Board(StrictFrozenModel):
    origin: Point
    width: ExactFloat
    height: ExactFloat


class V6Terminal(StrictFrozenModel):
    point: Point


class V6Node(StrictFrozenModel):
    point: Point


class V6Edge(StrictFrozenModel):
    endpoints: EndpointTuple
    routing_class: RoutingClass
    target_length: ExactFloat | None = None
    width: ExactFloat | None = None

    @model_validator(mode="after")
    def validate_target_length(self) -> Self:
        if self.routing_class is RoutingClass.RF_CONSTRAINED_LOCKED:
            if self.target_length is None:
                raise ValueError(
                    "target_length is required when routing_class is rf_constrained_locked"
                )
            return self

        if self.target_length is not None:
            raise ValueError(
                "target_length is only allowed when routing_class is rf_constrained_locked"
            )
        return self


class V6IR(StrictFrozenModel):
    board: Board
    terminals: dict[str, V6Terminal] = Field(default_factory=dict)
    nodes: dict[str, V6Node] = Field(default_factory=dict)
    edges: dict[str, V6Edge] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_edge_endpoints(self) -> Self:
        valid_endpoints = set(self.terminals) | set(self.nodes)
        for edge_id, edge in self.edges.items():
            source, target = edge.endpoints
            missing = [
                label
                for label, endpoint in (("source", source), ("target", target))
                if endpoint not in valid_endpoints
            ]
            if missing:
                missing_labels = ", ".join(missing)
                raise ValueError(
                    f"edge {edge_id} endpoints reference undefined {missing_labels}"
                )
        return self


__all__ = [
    "Board",
    "Point",
    "RoutingClass",
    "StrictFrozenModel",
    "V6Edge",
    "V6IR",
    "V6Node",
    "V6Terminal",
]
