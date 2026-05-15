from pathlib import Path
import json
import math

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
    assert "<title>IC1_pin1_seg1 | w=3.6mm | L=5.8mm</title>" in svg
    assert 'class="prea-virtual-endpoint"' in svg
    assert 'class="prea-rlc-bbox"' in svg
    assert "<title>C1.PIN_1</title>" in svg
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
    seg3_render = by_edge["IC1_pin1_seg3"]["render_endpoint_positions_mm"]
    seg3_start = seg3_render["IC1_pin1_seg1_universal_node"]
    seg3_end = seg3_render["IC1_pin1_seg3_end_split_pad"]
    seg1_w = float(by_edge["IC1_pin1_seg1"]["width"])
    # offset_v=edge_left and offset_u=-3.94 on a downward reference edge:
    # launch point shifts +x (left edge) and +y (back from front edge).
    assert seg2_start["x"] - pin1_node["x"] == pytest.approx(seg1_w / 2.0, abs=0.15)
    assert seg2_start["y"] - pin1_node["y"] == pytest.approx(3.94, abs=0.2)
    assert seg3_start["x"] - pin1_node["x"] == pytest.approx(seg1_w / 2.0, abs=0.15)
    assert seg3_start["y"] - pin1_node["y"] == pytest.approx(1.6, abs=0.2)
    # angle=90 means seg2 runs to the right from its launch point.
    assert seg2_end["x"] > seg2_start["x"]
    assert seg2_end["y"] == pytest.approx(seg2_start["y"], abs=0.2)
    assert seg3_end["x"] > seg3_start["x"]
    assert seg3_end["y"] == pytest.approx(seg3_start["y"], abs=0.2)

    # IC1 PIN_1 seg4: angle=0 + edge_front + align_left
    # => same direction as seg1, launch from seg1 front edge, left-edge aligned.
    seg4_render = by_edge["IC1_pin1_seg4"]["render_endpoint_positions_mm"]
    seg4_start = seg4_render["IC1_pin1_seg1_universal_node"]
    seg4_end_key = next(k for k in seg4_render if k != "IC1_pin1_seg1_universal_node")
    seg4_end = seg4_render[seg4_end_key]
    # left-edge alignment: center shifts by (w_ref - w_seg4)/2 = (3.6-1.6)/2 = 1.0mm
    assert seg4_start["x"] - pin1_node["x"] == pytest.approx(1.0, abs=0.15)
    # front-edge connection: no backward/forward offset from node center.
    assert seg4_start["y"] - pin1_node["y"] == pytest.approx(0.0, abs=0.15)
    # angle=0: seg4 runs same direction as seg1 (downward).
    assert seg4_end["x"] == pytest.approx(seg4_start["x"], abs=0.2)
    assert seg4_end["y"] < seg4_start["y"]

    # PreA prioritizes electrical connectivity: free branch to R2 PIN_2 should
    # land on seg2 split endpoint. If package geometry cannot fully satisfy all
    # pin targets, render should show a thin assist link.
    seg2_to_r2 = by_edge["IC1_pin1_seg2_to_R2"]["render_endpoint_positions_mm"]
    split_xy = seg2_to_r2["IC1_pin1_seg2_end_split_pad"]
    r2_pin2_xy = seg2_to_r2["R2.PIN_2"]
    assert r2_pin2_xy["x"] == pytest.approx(split_xy["x"], abs=1e-6)
    assert r2_pin2_xy["y"] == pytest.approx(split_xy["y"], abs=1e-6)
    c1r1_to_combiner = by_edge["C1R1_to_combiner"]["render_endpoint_positions_mm"]
    c1r1_pin2 = c1r1_to_combiner["C1.PIN_2,R1.PIN_2"]
    seg5_start = c1r1_to_combiner["IC1_pin1_seg5_start_combiner"]
    assert c1r1_pin2["x"] == pytest.approx(seg5_start["x"], abs=1e-6)
    assert c1r1_pin2["y"] == pytest.approx(seg5_start["y"], abs=1e-6)
    c1r1_from_seg4 = by_edge["IC1_pin1_seg4_to_C1R1"]["render_endpoint_positions_mm"][
        "C1.PIN_1,R1.PIN_1"
    ]
    # C1/R1 are two-pin passives; pin pitch should follow package scale, and
    # the downstream trace endpoint should move to keep compact connectivity.
    c1r1_span = math.hypot(
        c1r1_pin2["x"] - c1r1_from_seg4["x"],
        c1r1_pin2["y"] - c1r1_from_seg4["y"],
    )
    assert c1r1_span < 3.0
    seg5_render = by_edge["IC1_pin1_seg5"]["render_endpoint_positions_mm"]
    seg5_len = math.hypot(
        seg5_render["C2.PIN_1"]["x"] - seg5_render["IC1_pin1_seg5_start_combiner"]["x"],
        seg5_render["C2.PIN_1"]["y"] - seg5_render["IC1_pin1_seg5_start_combiner"]["y"],
    )
    assert seg5_len == pytest.approx(4.72, abs=0.35)
    assert ">p1_seg2<" in svg
    assert "p1_seg2-&gt;R2" not in svg
    assert 'class="prea-edge-bridge"' in svg
    assert 'class="prea-edge-bridge" data-edge-id="RF_INPUT_to_IC1"' in svg
    assert 'class="prea-edge-bridge" data-edge-id="PWR_VDD_bus"' in svg
    assert 'data-endpoint="C1.PIN_1"' not in svg
    assert 'data-endpoint="C1.PIN_2"' not in svg
    assert 'data-endpoint="R1.PIN_1"' not in svg
    assert 'data-endpoint="R1.PIN_2"' not in svg
    assert by_edge["IC1_pin1_seg2_to_R2"]["edge_short"] == "p1_seg2->R2"

    phase_a_svg = scratch_dir / "PA_Module_Simplified.phaseA.svg"
    phase_a_text = phase_a_svg.read_text(encoding="utf-8")
    assert 'stroke-linecap="butt"' in phase_a_text
    assert 'stroke-linecap="round"' not in phase_a_text

    phase_b_svg = scratch_dir / "PA_Module_Simplified.phaseB.svg"
    phase_b_text = phase_b_svg.read_text(encoding="utf-8")
    assert 'class="component-bbox"' in phase_b_text
    assert "<title>C1.PIN_1</title>" in phase_b_text
