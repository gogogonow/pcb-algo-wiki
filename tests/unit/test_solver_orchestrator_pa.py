"""Subprocess smoke + Python-API tests for the M5 orchestrator and pcb_solve CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PA_YAML = REPO_ROOT / "rf_layout_simplified.yaml"


def test_orchestrator_pa_optimal_in_one_attempt() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    from solver.orchestrator import OrchestratorOptions, solve_layout

    result = solve_layout(
        PA_YAML, options=OrchestratorOptions(time_limit_s=10.0, num_workers=4)
    )
    assert result.geometry.solve_status in ("OPTIMAL", "FEASIBLE")
    assert result.attempts == 1
    assert result.audit.locked_edges_within_tolerance is True
    assert result.audit.max_length_error_fraction <= 0.005 + 1e-9
    assert result.sa is not None and result.sa.placements


def test_orchestrator_no_sa_still_feasible() -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    from solver.orchestrator import OrchestratorOptions, solve_layout

    result = solve_layout(
        PA_YAML,
        options=OrchestratorOptions(use_sa=False, time_limit_s=10.0, num_workers=4),
    )
    assert result.geometry.solve_status in ("OPTIMAL", "FEASIBLE")
    assert result.sa is None


def test_pcb_solve_cli_writes_svg_and_report(tmp_path: Path) -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    svg = tmp_path / "out.svg"
    report = tmp_path / "report.json"
    cmd = [
        sys.executable,
        "-m",
        "tools.pcb_solve",
        str(PA_YAML),
        "--time-limit",
        "10",
        "--workers",
        "4",
        "--quiet",
        "--svg-out",
        str(svg),
        "--report-out",
        str(report),
    ]
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert (
        proc.returncode == 0
    ), f"pcb_solve failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert svg.exists()
    assert svg.read_text(encoding="utf-8").startswith("<svg")
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["solve_status"] in ("OPTIMAL", "FEASIBLE")
    assert payload["locked_edges_within_tolerance"] is True
    assert payload["max_length_error_fraction"] <= 0.005 + 1e-9
    assert payload["attempts"] >= 1
    assert payload["sa"] is not None
    assert "astar" in payload
