"""Unit tests for the M3 universal_junction template expansion."""

from __future__ import annotations

import math

import pytest

from frontend.universal_junction import expand_universal_junctions
from schema.solver_ir import SignedVKind
from schema.v33 import V33Layout


def _layout(
    angle: float,
    offset_v: str,
    *,
    offset_u: object = 1.0,
    width: float = 1.0,
    clearance: float = 0.2,
) -> V33Layout:
    return V33Layout.model_validate(
        {
            "metadata": {"project_name": "Test"},
            "global_constraints": {
                "board_outline": {
                    "type": "bounding_box",
                    "origin": {"x": 0.0, "y": 0.0},
                    "width": 10.0,
                    "height": 10.0,
                },
                "routing": {
                    "default_trace_width": 0.5,
                    "default_clearance": clearance,
                },
            },
            "footprints": {},
            "components": {},
            "nodes": {
                "J1": {
                    "type": "universal_junction",
                    "connection_rules": {
                        "reference_edge": "E_REF",
                        "branches": [
                            {
                                "edge": "E1",
                                "angle": angle,
                                "origin": {"offset_u": offset_u, "offset_v": offset_v},
                            }
                        ],
                    },
                },
            },
            "terminals": {},
            "edges": {
                "E_REF": {
                    "type": "microstrip",
                    "net": "N",
                    "connections": ["J1", "OTHER"],
                    "constraint": {"width": width, "target_length": 1.0},
                },
                "E1": {
                    "type": "microstrip",
                    "net": "N",
                    "connections": ["J1", "FAR"],
                    "constraint": {"width": width, "target_length": 1.0},
                },
            },
        }
    )


@pytest.mark.parametrize(
    "angle,offset_v,offset_u,expected_kind,expected_signed_v_sign,expected_dx,expected_dy",
    [
        # angle=0, align_center: dx=offset_u, dy=0
        (0.0, "align_center", 2.0, SignedVKind.ALIGN_CENTER, 0, 2.0, 0.0),
        # angle=90, edge_left: signed_v=+side; dx=-signed_v, dy=offset_u
        (90.0, "edge_left", 1.0, SignedVKind.EDGE_LEFT, 1, None, 1.0),
        # angle=-90, edge_right: signed_v=-side; dx=signed_v? cos(-90)=0,sin(-90)=-1
        # dx = 0*offset_u - (-1)*signed_v = signed_v (negative); dy = -1*offset_u
        (-90.0, "edge_right", 1.0, SignedVKind.EDGE_RIGHT, -1, None, -1.0),
        # angle=180, edge_left: cos=-1, sin=0; dx=-offset_u, dy=-signed_v
        (180.0, "edge_left", 1.5, SignedVKind.EDGE_LEFT, 1, -1.5, None),
    ],
)
def test_branch_geometry(
    angle: float,
    offset_v: str,
    offset_u: float,
    expected_kind: SignedVKind,
    expected_signed_v_sign: int,
    expected_dx: float | None,
    expected_dy: float | None,
) -> None:
    layout = _layout(angle, offset_v, offset_u=offset_u, width=1.0, clearance=0.2)
    templates = expand_universal_junctions(layout)
    assert "J1" in templates
    branch = templates["J1"].branches[0]
    assert branch.signed_v_kind is expected_kind
    if expected_signed_v_sign == 0:
        assert branch.signed_v == 0.0
    else:
        assert math.copysign(1.0, branch.signed_v) == expected_signed_v_sign
        assert abs(branch.signed_v) == pytest.approx(1.0 / 2.0 + 0.2)
    if expected_dx is not None:
        assert branch.dx == pytest.approx(expected_dx)
    if expected_dy is not None:
        assert branch.dy == pytest.approx(expected_dy)


@pytest.mark.parametrize("token", ["align_left", "align_center", "align_right"])
def test_symbolic_offset_u_tokens_resolve_to_zero(token: str) -> None:
    layout = _layout(0.0, "edge_front", offset_u=token)
    templates = expand_universal_junctions(layout)
    branch = templates["J1"].branches[0]
    assert branch.offset_u == 0.0
    assert branch.signed_v_kind is SignedVKind.EDGE_FRONT
    assert branch.signed_v == 0.0
    assert branch.dx == 0.0
    assert branch.dy == 0.0


def test_target_endpoint_is_far_side() -> None:
    layout = _layout(0.0, "align_center")
    templates = expand_universal_junctions(layout)
    branch = templates["J1"].branches[0]
    assert branch.target_endpoint == "FAR"
    assert branch.edge_id == "E1"
    assert branch.branch_index == 0


def test_unknown_edge_raises() -> None:
    layout = _layout(0.0, "align_center")
    # mutate to point at missing edge
    layout.nodes["J1"].connection_rules.branches[0].edge = "MISSING"
    with pytest.raises(ValueError, match="unknown edge"):
        expand_universal_junctions(layout)


def test_unknown_offset_v_raises() -> None:
    layout = _layout(0.0, "align_center")
    layout.nodes["J1"].connection_rules.branches[0].origin.offset_v = "weird"
    with pytest.raises(ValueError, match="unknown universal_junction offset_v"):
        expand_universal_junctions(layout)


def test_non_universal_node_skipped() -> None:
    layout = _layout(0.0, "align_center")
    layout.nodes["J1"].type = "t_junction"
    templates = expand_universal_junctions(layout)
    assert templates == {}
