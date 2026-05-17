import sys
from pathlib import Path
import subprocess

from schema import load_v33_layout
from tools import schema_check

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_main_reports_real_case_counts(capsys) -> None:
    layout = load_v33_layout(REAL_CASE_PATH)
    exit_code = schema_check.main([str(REAL_CASE_PATH)])

    assert exit_code == 0

    captured = capsys.readouterr()
    assert "PA_Module_Simplified" in captured.out
    assert f"components: {len(layout.components)}" in captured.out
    assert f"footprints: {len(layout.footprints)}" in captured.out
    assert f"nodes: {len(layout.nodes)}" in captured.out
    assert f"terminals: {len(layout.terminals)}" in captured.out
    assert f"edges: {len(layout.edges)}" in captured.out


def test_module_invocation_reports_real_case_counts() -> None:
    layout = load_v33_layout(REAL_CASE_PATH)
    result = subprocess.run(
        [sys.executable, "-m", "tools.schema_check", "rf_layout_simplified.yaml"],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (
        "project: PA_Module_Simplified\n"
        f"components: {len(layout.components)}\n"
        f"footprints: {len(layout.footprints)}\n"
        f"nodes: {len(layout.nodes)}\n"
        f"terminals: {len(layout.terminals)}\n"
        f"edges: {len(layout.edges)}\n"
    )


def test_module_invocation_for_missing_input_exits_cleanly() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.schema_check", "missing-input.yaml"],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "missing-input.yaml" in result.stderr
    assert "Traceback" not in result.stderr
