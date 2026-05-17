"""Smoke test for the v2 orchestrator on PA_Module_Simplified."""

from __future__ import annotations

from pathlib import Path

import pytest

from solver.v2 import OrchestratorV2Options, solve_layout_v2
from solver.v2.orchestrator import phase_summary

YAML_PATH = Path(__file__).resolve().parents[3] / "rf_layout_simplified.yaml"


@pytest.mark.skipif(not YAML_PATH.exists(), reason="PA yaml missing")
def test_skeleton_first_runs_end_to_end() -> None:
    options = OrchestratorV2Options(
        clearance_mm=0.15, grid_step_um=200, rip_up_rounds=3
    )
    result = solve_layout_v2(YAML_PATH, options=options)
    summary = phase_summary(result)
    # We expect at least one route to succeed and Phase B to produce
    # placements for every UV component.
    assert summary["phase_a"]["routed"] >= 1
    assert summary["phase_b"]["uv_placed"] == summary["phase_b"]["uv_total"]
    # Phase C must be present in the summary. After the floating-placer
    # crossing-penalty upgrade (SA energy now includes RF-bus crossing,
    # airwire-airwire crossing, and body-body overlap with connected-pair
    # exemption), the PA case routes >=4 of 6 flexible edges reliably.
    assert summary["phase_c"]["flex_routed"] >= 4
    assert "drc_violations" in summary["phase_c"]
    assert "route_total_length_mm" in summary
    assert "route_total_segments" in summary
    assert "wall_s" in summary["phase_c"]
    # Status must be one of the schema-allowed values.
    assert result.geometry.solve_status in {
        "OPTIMAL",
        "FEASIBLE",
        "INFEASIBLE",
        "UNKNOWN",
        "MODEL_INVALID",
    }
    # Wall time guardrail (loose; tightened in M10c).
    assert summary["wall_total_s"] < 60.0
