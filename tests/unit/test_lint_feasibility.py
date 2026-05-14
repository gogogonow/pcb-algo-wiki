"""M7 semantic lint tests — pin_multi_net and length_feasibility rules."""

from __future__ import annotations


from frontend.lint import lint_semantic
from frontend.models import ExpandedPad
from schema.v33 import V33Layout


def _pad(comp: str, pin: str, x: float, y: float) -> ExpandedPad:
    return ExpandedPad(
        component=comp, pin=pin, abs_x=x, abs_y=y, orientation=0.0, kind="fixed"
    )


def _minimal_layout(raw_extra: dict) -> V33Layout:
    """Build a minimal V33Layout from a partial raw dict (components + edges)."""
    base: dict = {
        "metadata": {"project_name": "test", "version": "3.3"},
        "global_constraints": {
            "routing": {"default_trace_width": 0.254, "default_clearance": 0.15},
            "board_outline": {
                "type": "rect",
                "origin": {"x": 0, "y": 0},
                "width": 100,
                "height": 100,
            },
        },
        "footprints": {},
        "components": raw_extra.get("components", {}),
        "nodes": {},
        "terminals": {},
        "edges": raw_extra.get("edges", {}),
    }
    return V33Layout.model_validate(base)


# ──────────────────────────────────────────────────────────────────────────────
# pin_multi_net tests
# ──────────────────────────────────────────────────────────────────────────────


def test_pin_multi_net_clean() -> None:
    """No conflict: each terminal appears in at most one net."""
    layout = _minimal_layout(
        {
            "components": {"IC1": {"pin_nets": {"PIN_1": "NET_A"}}},
            "edges": {
                "E1": {"net": "NET_A", "connections": ["IC1.PIN_1", "TP1.PIN_1"]},
            },
        }
    )
    fixed: dict[str, ExpandedPad] = {}
    errors, warnings = lint_semantic(layout, fixed)
    assert not errors
    assert not warnings


def test_pin_multi_net_conflict() -> None:
    """IC1.PIN_3 in both NET_A and NET_B → pin_multi_net error."""
    layout = _minimal_layout(
        {
            "components": {
                "IC1": {"pin_nets": {"PIN_3": "NET_A"}},
            },
            "edges": {
                "E1": {"net": "NET_A", "connections": ["IC1.PIN_3", "TP1.PIN_1"]},
                "E2": {"net": "NET_B", "connections": ["IC1.PIN_3", "TP2.PIN_1"]},
            },
        }
    )
    fixed: dict[str, ExpandedPad] = {}
    errors, warnings = lint_semantic(layout, fixed)
    assert len(errors) == 1
    assert errors[0].code == "pin_multi_net"
    assert "IC1.PIN_3" in errors[0].message
    assert "NET_A" in errors[0].message
    assert "NET_B" in errors[0].message


def test_pin_multi_net_node_not_flagged() -> None:
    """Connections that look like node IDs (not real comp.pin) are ignored."""
    layout = _minimal_layout(
        {
            "components": {},
            "edges": {
                "E1": {
                    "net": "NET_A",
                    "connections": ["IC1_pin1_some_node", "IC1_pin1_other_node"],
                },
                "E2": {"net": "NET_B", "connections": ["IC1_pin1_some_node"]},
            },
        }
    )
    fixed: dict[str, ExpandedPad] = {}
    errors, warnings = lint_semantic(layout, fixed)
    # IC1_pin1_some_node has no "." → not a comp terminal → no error
    assert not errors


# ──────────────────────────────────────────────────────────────────────────────
# length_feasibility tests
# ──────────────────────────────────────────────────────────────────────────────


def test_length_infeasible_short_raises_error() -> None:
    """Target=5mm for endpoints 50mm apart → length_infeasible_short."""
    layout = _minimal_layout(
        {
            "components": {"IC1": {}, "TP1": {}},
            "edges": {
                "E1": {
                    "net": "RF",
                    "connections": ["IC1.PIN_1", "TP1.PIN_1"],
                    "constraint": {"target_length": 5.0},
                }
            },
        }
    )
    fixed = {
        "IC1.PIN_1": _pad("IC1", "PIN_1", 0.0, 0.0),
        "TP1.PIN_1": _pad("TP1", "PIN_1", 30.0, 20.0),  # Manhattan = 50mm
    }
    errors, warnings = lint_semantic(layout, fixed)
    assert len(errors) == 1
    assert errors[0].code == "length_infeasible_short"
    assert "5.0mm" in errors[0].message


def test_meander_required_warning() -> None:
    """Target=60mm for endpoints 50mm apart → meander_required warning."""
    layout = _minimal_layout(
        {
            "components": {"IC1": {}, "TP1": {}},
            "edges": {
                "E1": {
                    "net": "RF",
                    "connections": ["IC1.PIN_1", "TP1.PIN_1"],
                    "constraint": {"target_length": 60.0},
                }
            },
        }
    )
    fixed = {
        "IC1.PIN_1": _pad("IC1", "PIN_1", 0.0, 0.0),
        "TP1.PIN_1": _pad("TP1", "PIN_1", 30.0, 20.0),  # Manhattan = 50mm
    }
    errors, warnings = lint_semantic(layout, fixed)
    assert not errors
    assert len(warnings) == 1
    assert warnings[0].code == "meander_required"
    assert "60.0mm" in warnings[0].message


def test_no_lint_for_uv_endpoints() -> None:
    """Edges with non-fixed (UV) endpoints → skipped (no abs_x)."""
    layout = _minimal_layout(
        {
            "components": {"C1": {}},
            "edges": {
                "E1": {
                    "net": "RF",
                    "connections": ["IC1.PIN_1", "C1.PIN_1"],
                    "constraint": {"target_length": 2.0},
                }
            },
        }
    )
    # C1.PIN_1 is UV (not in fixed_terminals), IC1.PIN_1 also missing → skip
    fixed: dict[str, ExpandedPad] = {}
    errors, warnings = lint_semantic(layout, fixed)
    assert not errors
    assert not warnings


def test_feasible_length_no_warning() -> None:
    """Target within ±0.5mm tolerance → no error, no meander warning."""
    layout = _minimal_layout(
        {
            "components": {"IC1": {}, "TP1": {}},
            "edges": {
                "E1": {
                    "net": "RF",
                    "connections": ["IC1.PIN_1", "TP1.PIN_1"],
                    "constraint": {"target_length": 50.2},  # within 0.5mm of 50mm
                }
            },
        }
    )
    fixed = {
        "IC1.PIN_1": _pad("IC1", "PIN_1", 0.0, 0.0),
        "TP1.PIN_1": _pad("TP1", "PIN_1", 30.0, 20.0),  # Manhattan = 50mm
    }
    errors, warnings = lint_semantic(layout, fixed)
    assert not errors
    assert not warnings
