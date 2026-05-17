"""WI-I7: phaseB SVG must show IC PIN labels, RLC component names (not pins), and no long ids."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_phase_b_svg_has_pin_short_labels_and_no_long_ids(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.tools.pcb_solve_v2",
            str(REPO / "rf_layout_simplified.yaml"),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
        cwd=str(REPO),
    )
    svg_path = out_dir / "PA_Module_Simplified.phaseB.svg"
    assert svg_path.exists(), f"phaseB svg missing: {svg_path}"
    svg = svg_path.read_text()
    # WI-I7: RLC component names appear (not PIN labels)
    assert ">C1<" in svg, "RLC component name C1 missing"
    assert ">C2<" in svg, "RLC component name C2 missing"
    # WI-I7: RLC PIN labels should NOT appear (user requested removal)
    assert ">C1.1<" not in svg, "RLC pin labels should be removed"
    assert ">C1.2<" not in svg, "RLC pin labels should be removed"
    assert ">C2.1<" not in svg, "RLC pin labels should be removed"
    # IC PIN labels appear
    assert ">PIN_1<" in svg, "IC PIN labels should appear"
    # Long engine ids must NOT appear in phaseB after WI-F1/F2.
    assert "IC1_pin1_seg2_end_split_pad" not in svg
    assert "IC1_pin1_seg5_start_combiner" not in svg
    assert "_universal_node" not in svg
