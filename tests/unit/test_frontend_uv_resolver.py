"""Unit tests for the M3 UV resolver."""

from __future__ import annotations

from frontend.expand_components import expand_components
from frontend.models import LintWarning
from frontend.uv_resolver import host_match_histogram, resolve_uv_components
from schema.solver_ir import PinPositionKind, UvHostMatch
from schema.v33 import V33Layout


def _build_layout(
    *,
    edges: dict[str, dict],
    components: dict[str, dict],
    footprints: dict[str, dict] | None = None,
) -> V33Layout:
    return V33Layout.model_validate(
        {
            "metadata": {"project_name": "T"},
            "global_constraints": {
                "board_outline": {
                    "type": "bounding_box",
                    "origin": {"x": 0.0, "y": 0.0},
                    "width": 10.0,
                    "height": 10.0,
                },
                "routing": {"default_trace_width": 0.5, "default_clearance": 0.2},
            },
            "footprints": footprints
            or {
                "FP_2P": {
                    "dimensions": {"width": 1.0, "length": 2.0, "height": 0.5},
                    "pins": {
                        "PIN_1": {"local_x": 0.0, "local_y": -1.0},
                        "PIN_2": {"local_x": 0.0, "local_y": 1.0},
                    },
                },
            },
            "components": components,
            "nodes": {},
            "terminals": {},
            "edges": edges,
        }
    )


def _uv_component(*, anchor_pin: str = "PIN_1", reference_net: str = "N") -> dict:
    return {
        "footprint_ref": "FP_2P",
        "placement": {
            "type": "parametric_uv",
            "anchor_pin": anchor_pin,
            "reference_net": reference_net,
            "origin": {"offset_u": 0.0, "offset_v": "edge_left"},
        },
        "pin_nets": {"PIN_1": reference_net, "PIN_2": "OTHER"},
    }


def test_unique_host_match() -> None:
    layout = _build_layout(
        edges={
            "EH": {
                "type": "microstrip",
                "net": "N",
                "connections": ["C1.PIN_1", "OTHER.PIN_1"],
                "constraint": {"width": 0.6, "target_length": 5.0},
            },
        },
        components={"C1": _uv_component()},
    )
    resolutions = resolve_uv_components(layout, expand_components(layout))
    assert "C1" in resolutions
    res = resolutions["C1"]
    assert res.host_match_status is UvHostMatch.UNIQUE
    assert res.host_edge_id == "EH"
    assert res.host_candidates == ("EH",)
    assert not res.synthetic_host
    assert res.host_width == 0.6
    # Should produce two pin position expressions
    kinds = {expr.pin: expr.kind for expr in res.pin_position_exprs}
    assert kinds["PIN_1"] is PinPositionKind.ANCHOR
    assert kinds["PIN_2"] is PinPositionKind.DERIVED


def test_ambiguous_host_match() -> None:
    layout = _build_layout(
        edges={
            "E1": {
                "type": "microstrip",
                "net": "N",
                "connections": ["C1.PIN_1", "X1"],
                "constraint": {"width": 0.6, "target_length": 5.0},
            },
            "E2": {
                "type": "microstrip",
                "net": "N",
                "connections": ["C1.PIN_1", "X2"],
                "constraint": {"width": 0.6, "target_length": 5.0},
            },
        },
        components={"C1": _uv_component()},
    )
    res = resolve_uv_components(layout, expand_components(layout))["C1"]
    assert res.host_match_status is UvHostMatch.AMBIGUOUS
    assert len(res.host_candidates) == 2


def test_missing_host_emits_warning_and_synthetic() -> None:
    layout = _build_layout(
        edges={
            "E_OTHER_NET": {
                "type": "microstrip",
                "net": "OTHER_NET",
                "connections": ["C1.PIN_1", "X"],
                "constraint": {"width": 0.6, "target_length": 5.0},
            },
        },
        components={"C1": _uv_component(reference_net="MISSING_NET")},
    )
    warnings: list[LintWarning] = []
    res = resolve_uv_components(layout, expand_components(layout), warnings=warnings)[
        "C1"
    ]
    assert res.host_match_status is UvHostMatch.MISSING
    assert res.synthetic_host is True
    assert res.host_edge_id.startswith("synthetic_host__")
    assert len(warnings) == 1
    assert warnings[0].code == "uv_host_missing"


def test_histogram() -> None:
    layout = _build_layout(
        edges={
            "EH": {
                "type": "microstrip",
                "net": "N",
                "connections": ["C1.PIN_1", "X"],
                "constraint": {"width": 0.6, "target_length": 5.0},
            },
        },
        components={"C1": _uv_component()},
    )
    resolutions = resolve_uv_components(layout, expand_components(layout))
    hist = host_match_histogram(resolutions.values())
    assert hist["unique"] == 1
    assert hist["ambiguous"] == 0
    assert hist["missing"] == 0


def test_skips_non_uv_components() -> None:
    layout = _build_layout(
        edges={},
        components={
            "F1": {
                "footprint_ref": "FP_2P",
                "placement": {"type": "fixed", "x": 1.0, "y": 1.0, "rotation": 0.0},
                "pin_nets": {"PIN_1": "A", "PIN_2": "B"},
            }
        },
    )
    resolutions = resolve_uv_components(layout, expand_components(layout))
    assert resolutions == {}
