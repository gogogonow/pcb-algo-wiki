"""Unit tests for the strict M3 SolverIR pydantic schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schema.solver_ir import (
    BranchConstraint,
    PinPositionKind,
    PinPositionExpr,
    RotationDomain,
    SignedVKind,
    SolverEdge,
    SolverIR,
    UniversalJunctionTemplate,
    UvHostMatch,
    UvResolution,
)
from schema.v6_ir import Board, Point, RoutingClass, V6Terminal


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=10.0, height=10.0)


def _good_uv(
    host_id: str = "EH",
    *,
    status: UvHostMatch = UvHostMatch.UNIQUE,
    synthetic: bool = False,
) -> UvResolution:
    candidates: tuple[str, ...]
    if status is UvHostMatch.UNIQUE:
        candidates = (host_id,)
    elif status is UvHostMatch.AMBIGUOUS:
        candidates = (host_id, "OTHER")
    else:
        candidates = ()
    return UvResolution(
        component="C1",
        anchor_pin="PIN_1",
        reference_net="N",
        host_match_status=status,
        host_edge_id=host_id,
        host_candidates=candidates,
        rotation_domain=RotationDomain(values=(0, 90, 180, 270)),
        offset_v_sides=(-1, 1),
        host_width=0.5,
        clearance=0.2,
        pin_position_exprs=(),
        synthetic_host=synthetic,
    )


def _good_edge() -> SolverEdge:
    return SolverEdge(
        endpoints=("A", "B"),
        routing_class=RoutingClass.RF_CONSTRAINED_FREE,
        net="N",
    )


def test_solver_ir_round_trip() -> None:
    ir = SolverIR(
        project="P",
        board=_board(),
        clearance=0.2,
        terminals={"T1": V6Terminal(point=Point(x=0.0, y=0.0))},
        edges={"EH": _good_edge()},
        uv_resolutions={"C1": _good_uv("EH")},
        junction_templates={},
    )
    assert ir.project == "P"


def test_negative_width_rejected() -> None:
    with pytest.raises(ValidationError):
        SolverEdge(
            endpoints=("A", "B"),
            routing_class=RoutingClass.RF_CONSTRAINED_FREE,
            width=-1.0,
        )


def test_locked_requires_target_length() -> None:
    with pytest.raises(ValidationError):
        SolverEdge(
            endpoints=("A", "B"),
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
        )


def test_offset_v_sides_must_be_signed() -> None:
    with pytest.raises(ValidationError):
        UvResolution(
            component="C1",
            anchor_pin="PIN_1",
            reference_net="N",
            host_match_status=UvHostMatch.UNIQUE,
            host_edge_id="EH",
            host_candidates=("EH",),
            rotation_domain=RotationDomain(values=(0,)),
            offset_v_sides=(0,),
            host_width=0.5,
            clearance=0.2,
            pin_position_exprs=(),
            synthetic_host=False,
        )


def test_missing_status_requires_synthetic_host() -> None:
    with pytest.raises(ValidationError):
        _good_uv(status=UvHostMatch.MISSING, synthetic=False)


def test_unique_status_requires_one_candidate() -> None:
    with pytest.raises(ValidationError):
        UvResolution(
            component="C1",
            anchor_pin="PIN_1",
            reference_net="N",
            host_match_status=UvHostMatch.UNIQUE,
            host_edge_id="EH",
            host_candidates=("EH", "X"),  # too many for unique
            rotation_domain=RotationDomain(values=(0,)),
            offset_v_sides=(-1, 1),
            host_width=0.5,
            clearance=0.2,
            pin_position_exprs=(),
            synthetic_host=False,
        )


def test_solver_ir_rejects_unknown_host_edge_ref() -> None:
    with pytest.raises(ValidationError):
        SolverIR(
            project="P",
            board=_board(),
            clearance=0.2,
            terminals={},
            edges={},  # EH not present
            uv_resolutions={"C1": _good_uv("EH")},
            junction_templates={},
        )


def test_solver_ir_allows_synthetic_host_without_edge() -> None:
    uv = UvResolution(
        component="C1",
        anchor_pin="PIN_1",
        reference_net="N",
        host_match_status=UvHostMatch.MISSING,
        host_edge_id="synthetic_host__C1",
        host_candidates=(),
        rotation_domain=RotationDomain(values=(0,)),
        offset_v_sides=(-1, 1),
        host_width=0.5,
        clearance=0.2,
        pin_position_exprs=(),
        synthetic_host=True,
    )
    SolverIR(
        project="P",
        board=_board(),
        clearance=0.2,
        terminals={},
        edges={},
        uv_resolutions={"C1": uv},
        junction_templates={},
    )


def test_branch_constraint_invariants() -> None:
    bc = BranchConstraint(
        branch_index=0,
        edge_id="E1",
        target_endpoint="X",
        angle_deg=90.0,
        offset_u=1.0,
        signed_v_kind=SignedVKind.EDGE_LEFT,
        signed_v=0.45,
        dx=-0.45,
        dy=1.0,
    )
    tpl = UniversalJunctionTemplate(
        node_id="J1", reference_edge="E_REF", branches=(bc,)
    )
    assert tpl.branches[0].dx == -0.45


def test_universal_junction_requires_branches() -> None:
    with pytest.raises(ValidationError):
        UniversalJunctionTemplate(node_id="J1", reference_edge="E", branches=())


def test_pin_expression_round_trip() -> None:
    expr = PinPositionExpr(
        component="C1",
        pin="PIN_1",
        kind=PinPositionKind.ANCHOR,
        const_x=0.0,
        const_y=0.0,
        terms_x=(),
        terms_y=(),
    )
    assert expr.kind is PinPositionKind.ANCHOR
