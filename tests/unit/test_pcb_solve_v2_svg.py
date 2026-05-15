from pathlib import Path

from tools import pcb_solve_v2
from tools.pcb_solve_v2 import _pin_label_overlay

from schema.geometry_ir import ComponentPlacement, GeometryIR, PinPlacement
from schema.v6_ir import Board, Point

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_pin_label_overlay_renders_pin_numbers() -> None:
    geom = GeometryIR(
        project="pin_overlay",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=10.0),
        placements={
            "U1": ComponentPlacement(
                component="U1",
                anchor=Point(x=5.0, y=5.0),
                rotation_deg=0.0,
                pads=(
                    PinPlacement(pin="P1", point=Point(x=4.5, y=5.0)),
                    PinPlacement(pin="PIN_2", point=Point(x=5.5, y=5.0)),
                ),
            )
        },
        solve_status="FEASIBLE",
        solve_wall_seconds=0.0,
    )

    overlay = _pin_label_overlay(geom)

    assert "PIN_1" in overlay
    assert "PIN_2" in overlay


def test_main_writes_pre_phase_a_yaml_connectivity_svg(scratch_dir: Path) -> None:
    exit_code = pcb_solve_v2.main(
        [str(REAL_CASE_PATH), "--out-dir", str(scratch_dir), "--quiet"]
    )
    pre_a_svg = scratch_dir / "PA_Module_Simplified.preA.svg"

    assert exit_code == 0
    assert pre_a_svg.exists()
    svg = pre_a_svg.read_text(encoding="utf-8")
    assert 'class="prea-edge"' in svg
    assert "RF_INPUT_to_IC1" in svg
    assert "w=3.6mm" in svg
    assert "L=80.0mm" in svg
    assert 'class="prea-virtual-endpoint"' in svg
