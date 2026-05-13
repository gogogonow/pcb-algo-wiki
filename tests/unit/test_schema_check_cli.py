from pathlib import Path

from tools import schema_check

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_main_reports_real_case_counts(capsys) -> None:
    exit_code = schema_check.main([str(REAL_CASE_PATH)])

    assert exit_code == 0

    captured = capsys.readouterr()
    assert "PA_Module_Simplified" in captured.out
    assert "components: 15" in captured.out
    assert "footprints: 5" in captured.out
    assert "nodes: 8" in captured.out
    assert "terminals: 10" in captured.out
    assert "edges: 22" in captured.out
