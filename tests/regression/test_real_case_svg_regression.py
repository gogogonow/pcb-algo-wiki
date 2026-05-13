from pathlib import Path

from tools.topology_viz import main

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"
EXPECTED_SVG_PATH = (
    Path(__file__).resolve().parent / "PA_Module_Simplified" / "topology.svg.expected"
)


def test_real_case_svg_matches_committed_regression_baseline(
    scratch_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(scratch_dir)

    exit_code = main([str(REAL_CASE_PATH)])

    actual_output_path = scratch_dir / "out" / "PA_Module_Simplified.topology.svg"

    assert exit_code == 0
    assert actual_output_path.exists()
    assert actual_output_path.read_text(
        encoding="utf-8"
    ) == EXPECTED_SVG_PATH.read_text(encoding="utf-8")
