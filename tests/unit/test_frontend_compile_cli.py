import json
from pathlib import Path
import subprocess

from tools import frontend_compile

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_main_writes_artifact_json(scratch_dir: Path, capsys) -> None:
    out_path = scratch_dir / "artifact.json"
    exit_code = frontend_compile.main([str(REAL_CASE_PATH), "--out", str(out_path)])
    assert exit_code == 0
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["project_name"] == "PA_Module_Simplified"
    captured = capsys.readouterr()
    assert "PA_Module_Simplified" in captured.out
    assert "fixed_pads:" in captured.out


def test_lint_only_skips_writing(scratch_dir: Path, capsys) -> None:
    exit_code = frontend_compile.main([str(REAL_CASE_PATH), "--lint-only"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "lint:" in captured.out


def test_module_invocation_fails_for_missing_input() -> None:
    result = subprocess.run(
        ["python3", "-m", "tools.frontend_compile", "missing.yaml"],
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        text=True,
    )
    assert result.returncode != 0
    assert "missing.yaml" in result.stderr
    assert "Traceback" not in result.stderr
