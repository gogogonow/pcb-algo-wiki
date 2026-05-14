"""CLI smoke tests for the M3 solver_ir tool."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    venv_bin = REPO_ROOT / ".venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "solver_ir.py"), *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_solver_ir_cli_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "ir.json"
    proc = _run(
        str(REPO_ROOT / "rf_layout_simplified.yaml"),
        "--out",
        str(out),
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["project"] == "PA_Module_Simplified"
    assert "uv_resolutions" in data
    assert "junction_templates" in data
    assert len(data["uv_resolutions"]) == 9
    assert len(data["junction_templates"]) == 2


def test_solver_ir_cli_summary_only(tmp_path: Path) -> None:
    proc = _run(
        str(REPO_ROOT / "rf_layout_simplified.yaml"),
        "--summary-only",
    )
    assert proc.returncode == 0, proc.stderr
    assert "unique=9" in proc.stdout
