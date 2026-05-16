"""WI-D5: phaseB SVG must render RLC bbox + pads + GND markers + short labels."""

from __future__ import annotations

from pathlib import Path

from tools.pcb_solve_v2 import solve_and_emit


def test_phase_b_svg_has_rlc_bbox_pads_and_gnd(tmp_path: Path) -> None:
    yaml_path = Path("rf_layout_simplified.yaml").resolve()
    out_dir = tmp_path / "out"
    solve_and_emit(yaml_path, out_dir)
    svg = (out_dir / "PA_Module_Simplified.phaseB.svg").read_text(encoding="utf-8")
    # bbox class reused from preA renderer
    assert "prea-rlc-bbox" in svg
    # pad rectangles
    assert "prea-rlc-pad" in svg
    # GND text marker for capacitors connected to GND
    assert ">GND<" in svg
    # short labels (e.g. C1, R1) appear once placements rendered
    assert ">C1<" in svg or ">C1 " in svg or ">C1.PIN_" in svg
    # WI-E4: phaseA/preA style labels (node IDs and edge short names) must
    # also appear in phaseB via _phase_a_diag_overlay.
    # The overlay renders text with fill="#1f2937" (edge short labels).
    assert 'fill="#1f2937"' in svg
