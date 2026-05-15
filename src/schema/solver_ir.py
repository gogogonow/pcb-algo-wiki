"""Strict pydantic models for the M3 SolverIR (CP-SAT input).

This is the "pre-solve" sibling of `v6_ir.V6IR`: it carries everything the
CP-SAT model builder needs to materialize variables and linear constraints,
without committing to any particular geometric solution.

Per ITERATION-PLAN.md §M3:
- UV components contribute 4 IntVars each: anchor_x, anchor_y, rotation,
  offset_v_side. SolverIR records the host_edge match outcome plus the
  derived pin-position expressions so M4 can translate them directly into
  OR-Tools `model.Add(...)` calls.
- universal_junction nodes contribute 2 IntVars each (center_x, center_y)
  and N branch constraints with precomputed (dx, dy) integer offsets.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    Field,
    StrictBool,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .v6_ir import (
    Board,
    ExactFloat,
    RoutingClass,
    StrictFrozenModel,
    V6Terminal,
    require_positive_optional_float,
)


class UvHostMatch(StrEnum):
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class SignedVKind(StrEnum):
    EDGE_LEFT = "edge_left"
    EDGE_RIGHT = "edge_right"
    ALIGN_CENTER = "align_center"
    EDGE_FRONT = (
        "edge_front"  # front-face connection: v-offset = 0 (same as align_center)
    )


class RotationDomain(StrictFrozenModel):
    values: tuple[int, ...]

    @field_validator("values")
    @classmethod
    def validate_unique_finite_rotations(
        cls, value: tuple[int, ...]
    ) -> tuple[int, ...]:
        if len(value) == 0:
            raise ValueError("rotation domain must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("rotation domain values must be unique")
        return value


class ExpressionTerm(StrictFrozenModel):
    """A single coefficient × variable term in a linear expression.

    `variable` is a symbolic name (e.g. `C1.anchor_x`); M4 resolves it to an
    OR-Tools IntVar at model-build time.
    """

    coefficient: ExactFloat
    variable: StrictStr


class PinPositionKind(StrEnum):
    ANCHOR = "anchor"
    DERIVED = "derived"


class PinPositionExpr(StrictFrozenModel):
    """Linear expression describing one component pin's (x, y) coordinates.

    `x = const_x + Σ terms_x[i].coefficient · var(terms_x[i].variable)`
    `y = const_y + Σ terms_y[i].coefficient · var(terms_y[i].variable)`
    """

    component: StrictStr
    pin: StrictStr
    kind: PinPositionKind
    const_x: ExactFloat
    const_y: ExactFloat
    terms_x: tuple[ExpressionTerm, ...]
    terms_y: tuple[ExpressionTerm, ...]


class UvResolution(StrictFrozenModel):
    component: StrictStr
    anchor_pin: StrictStr
    reference_net: StrictStr
    host_match_status: UvHostMatch
    host_edge_id: StrictStr
    host_candidates: tuple[StrictStr, ...]
    rotation_domain: RotationDomain
    offset_v_sides: tuple[int, ...]
    host_width: ExactFloat
    clearance: ExactFloat
    pin_position_exprs: tuple[PinPositionExpr, ...]
    synthetic_host: StrictBool

    @field_validator("offset_v_sides")
    @classmethod
    def validate_offset_v_sides(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        allowed = {-1, 1}
        if not value:
            raise ValueError("offset_v_sides must be non-empty")
        if any(side not in allowed for side in value):
            raise ValueError("offset_v_sides entries must be +1 or -1")
        return value

    @field_validator("host_width", "clearance")
    @classmethod
    def validate_positive(cls, value: float, info: ValidationInfo) -> float:
        if value <= 0.0:
            raise ValueError(f"{info.field_name} must be greater than 0")
        return value

    @model_validator(mode="after")
    def validate_synthetic_consistency(self) -> Self:
        if self.host_match_status is UvHostMatch.MISSING and not self.synthetic_host:
            raise ValueError("missing host_match_status requires synthetic_host=True")
        if (
            self.host_match_status is UvHostMatch.UNIQUE
            and len(self.host_candidates) != 1
        ):
            raise ValueError("unique host_match_status requires exactly one candidate")
        if (
            self.host_match_status is UvHostMatch.AMBIGUOUS
            and len(self.host_candidates) < 2
        ):
            raise ValueError("ambiguous host_match_status requires ≥2 candidates")
        return self


class BranchConstraint(StrictFrozenModel):
    branch_index: Annotated[int, Field(ge=0)]
    edge_id: StrictStr
    target_endpoint: StrictStr
    angle_deg: ExactFloat
    offset_u: ExactFloat
    signed_v_kind: SignedVKind
    signed_v: ExactFloat
    dx: ExactFloat
    dy: ExactFloat


class UniversalJunctionTemplate(StrictFrozenModel):
    node_id: StrictStr
    reference_edge: StrictStr
    branches: tuple[BranchConstraint, ...]

    @field_validator("branches")
    @classmethod
    def validate_branches_non_empty(
        cls, value: tuple[BranchConstraint, ...]
    ) -> tuple[BranchConstraint, ...]:
        if not value:
            raise ValueError("universal_junction template must have ≥1 branch")
        return value


class SolverEdge(StrictFrozenModel):
    """Edge view as consumed by CP-SAT — narrower than V6Edge in two ways:

    - endpoints are free strings (a synthetic_host endpoint may not yet be in
      the strict v6 terminal/node table);
    - target_length is allowed for any routing_class but only required for
      rf_constrained_locked.
    """

    endpoints: tuple[StrictStr, StrictStr]
    routing_class: RoutingClass
    net: StrictStr | None = None
    target_length: ExactFloat | None = None
    width: ExactFloat | None = None
    bend_style: StrictStr | None = None
    launch_rule: StrictStr | None = None

    @field_validator("target_length", "width")
    @classmethod
    def validate_positive_optional_lengths(
        cls, value: float | None, info: ValidationInfo
    ) -> float | None:
        return require_positive_optional_float(value, info.field_name)

    @model_validator(mode="after")
    def validate_locked_target_length(self) -> Self:
        if (
            self.routing_class is RoutingClass.RF_CONSTRAINED_LOCKED
            and self.target_length is None
        ):
            raise ValueError(
                "target_length is required when routing_class is rf_constrained_locked"
            )
        return self


class SolverIR(StrictFrozenModel):
    project: StrictStr
    board: Board
    clearance: ExactFloat
    terminals: dict[str, V6Terminal] = Field(default_factory=dict)
    edges: dict[str, SolverEdge] = Field(default_factory=dict)
    uv_resolutions: dict[str, UvResolution] = Field(default_factory=dict)
    junction_templates: dict[str, UniversalJunctionTemplate] = Field(
        default_factory=dict
    )

    @field_validator("clearance")
    @classmethod
    def validate_clearance_positive(cls, value: float) -> float:
        if value <= 0.0:
            raise ValueError("clearance must be greater than 0")
        return value

    @model_validator(mode="after")
    def validate_uv_host_edges_exist(self) -> Self:
        for comp_id, uv in self.uv_resolutions.items():
            if uv.synthetic_host:
                continue
            if uv.host_edge_id not in self.edges:
                raise ValueError(
                    f"uv_resolution[{comp_id}] references unknown host_edge_id "
                    f"{uv.host_edge_id!r}"
                )
        return self


__all__ = [
    "BranchConstraint",
    "ExpressionTerm",
    "PinPositionExpr",
    "PinPositionKind",
    "RotationDomain",
    "SignedVKind",
    "SolverEdge",
    "SolverIR",
    "UniversalJunctionTemplate",
    "UvHostMatch",
    "UvResolution",
]
