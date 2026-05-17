import sys
from pathlib import Path
import subprocess

from tools import schema_check

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_main_reports_real_case_counts(capsys) -> None:
    exit_code = schema_check.main([str(REAL_CASE_PATH)])

    assert exit_code == 0

    captured = capsys.readouterr()
    assert "PA_Module_Simplified" in captured.out
    assert "components: 17" in captured.out
    assert "footprints: 5" in captured.out
    assert "nodes: 8" in captured.out
    assert "terminals: 9" in captured.out
    assert "edges: 21" in captured.out


def test_module_invocation_reports_real_case_counts() -> None:
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
        "components: 17\n"
        "footprints: 5\n"
        "nodes: 8\n"
        "terminals: 9\n"
        "edges: 21\n"
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
