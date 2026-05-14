"""Subprocess smoke test for ``tools.cpsat_solve`` CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cpsat_solve_cli_writes_svg_and_report(tmp_path: Path) -> None:
    pytest.importorskip("ortools.sat.python.cp_model")
    svg = tmp_path / "out.svg"
    report = tmp_path / "report.json"
    cmd = [
        sys.executable,
        "-m",
        "tools.cpsat_solve",
        "rf_layout_simplified.yaml",
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
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        proc.returncode == 0
    ), f"cpsat_solve failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert svg.exists()
    assert svg.read_text(encoding="utf-8").startswith("<svg")
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["solve_status"] in ("OPTIMAL", "FEASIBLE")
    assert payload["locked_edges_within_tolerance"] is True
    assert payload["max_length_error_fraction"] <= 0.005 + 1e-9
