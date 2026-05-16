"""WI-F2: phaseB SVG must show RLC PIN short labels and no long ids."""

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
    # PIN short labels appear on RLC pads (short_id rule: ".PIN_" → ".")
    assert ">C1.1<" in svg or ">C1.2<" in svg, "RLC pin short labels missing"
    # Long engine ids must NOT appear in phaseB after WI-F1/F2.
    assert "IC1_pin1_seg2_end_split_pad" not in svg
    assert "IC1_pin1_seg5_start_combiner" not in svg
    assert "_universal_node" not in svg
