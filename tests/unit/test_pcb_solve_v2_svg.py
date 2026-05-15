from pathlib import Path
import json

import pytest

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
    assert "stroke-linecap:butt" in svg
    assert "stroke-linecap:round" not in svg

    pre_a_json = scratch_dir / "PA_Module_Simplified.preA.json"
    payload = json.loads(pre_a_json.read_text(encoding="utf-8"))
    assert "yaml_geometric_constraints_applied" in payload
    assert any(
        item.get("edge_id") == "IC1_pin1_seg2"
        and item.get("mode") == "strict_junction_rule"
        for item in payload["yaml_geometric_constraints_applied"]
    )

    by_edge = {edge["edge_id"]: edge for edge in payload["edges"]}
    pin1_seg2 = by_edge["IC1_pin1_seg2"]["endpoint_positions_mm"][
        "IC1_pin1_seg2_end_split_pad"
    ]
    pin1_seg3 = by_edge["IC1_pin1_seg3"]["endpoint_positions_mm"][
        "IC1_pin1_seg3_end_split_pad"
    ]
    pin2_seg2 = by_edge["IC1_pin2_seg2"]["endpoint_positions_mm"][
        "IC1_pin2_seg2_end_split_pad"
    ]
    pin2_seg3 = by_edge["IC1_pin2_seg3"]["endpoint_positions_mm"][
        "IC1_pin2_seg3_end_split_pad"
    ]
    assert abs(pin1_seg2["y"] - pin1_seg3["y"]) > 2.0
    assert abs(pin2_seg2["y"] - pin2_seg3["y"]) > 1.5

    # IC1 PIN_1 / PIN_2 local_orientation are -90 in YAML, so first segments
    # should launch downward (decreasing y in board coordinates).
    pin1 = by_edge["IC1_pin1_seg1"]["endpoint_positions_mm"]["IC1.PIN_1"]
    pin1_node = by_edge["IC1_pin1_seg1"]["endpoint_positions_mm"][
        "IC1_pin1_seg1_universal_node"
    ]
    pin2 = by_edge["IC1_pin2_seg1"]["endpoint_positions_mm"]["IC1.PIN_2"]
    pin2_node = by_edge["IC1_pin2_seg1"]["endpoint_positions_mm"][
        "IC1_pin2_seg1_universal_node"
    ]
    assert pin1_node["y"] < pin1["y"]
    assert pin2_node["y"] < pin2["y"]

    # IC1 PIN_1 seg2 branch should be interpreted relative to seg1 direction.
    seg2_render = by_edge["IC1_pin1_seg2"]["render_endpoint_positions_mm"]
    seg2_start = seg2_render["IC1_pin1_seg1_universal_node"]
    seg2_end = seg2_render["IC1_pin1_seg2_end_split_pad"]
    # offset_v=edge_left and offset_u=-3.94 on a downward reference edge:
    # launch point shifts +x (left edge) and +y (back from front edge).
    assert seg2_start["x"] - pin1_node["x"] == pytest.approx(1.09, abs=0.15)
    assert seg2_start["y"] - pin1_node["y"] == pytest.approx(3.94, abs=0.2)
    # angle=90 means seg2 runs to the right from its launch point.
    assert seg2_end["x"] > seg2_start["x"]
    assert seg2_end["y"] == pytest.approx(seg2_start["y"], abs=0.2)

    phase_a_svg = scratch_dir / "PA_Module_Simplified.phaseA.svg"
    phase_a_text = phase_a_svg.read_text(encoding="utf-8")
    assert 'stroke-linecap="butt"' in phase_a_text
    assert 'stroke-linecap="round"' not in phase_a_text
