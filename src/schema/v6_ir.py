"""Strict Pydantic models for the v6 IR schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoutingClass(StrEnum):
    RF_CONSTRAINED_LOCKED = "rf_constrained_locked"
    RF_CONSTRAINED_FREE = "rf_constrained_free"
    FLEXIBLE_PATH = "flexible_path"


class Point(StrictFrozenModel):
    x: float
    y: float


class Board(StrictFrozenModel):
    origin: Point
    width: float
    height: float


class V6Terminal(StrictFrozenModel):
    point: Point


class V6Node(StrictFrozenModel):
    point: Point


class V6Edge(StrictFrozenModel):
    endpoints: tuple[str, str]
    routing_class: RoutingClass
    target_length: float | None = None

    @model_validator(mode="after")
    def validate_target_length(self) -> Self:
        if (
            self.routing_class is RoutingClass.RF_CONSTRAINED_LOCKED
            and self.target_length is None
        ):
            raise ValueError(
                "target_length is required when routing_class is rf_constrained_locked"
            )
        return self


class V6IR(StrictFrozenModel):
    board: Board
    terminals: dict[str, V6Terminal] = Field(default_factory=dict)
    nodes: dict[str, V6Node] = Field(default_factory=dict)
    edges: dict[str, V6Edge] = Field(default_factory=dict)


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
