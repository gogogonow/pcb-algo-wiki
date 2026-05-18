from pathlib import Path
import json
import math
import re
from types import SimpleNamespace

import pytest

from tools import pcb_solve_v2
from tools.pcb_solve_v2 import _pin_label_overlay
from frontend.models import (
    ExpandedPad,
    FrontendArtifact,
    LintReport,
    NormalizedNode,
    TriagedEdge,
)

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
    assert (
        ".prea-label{fill:#0f172a;font-family:Arial,sans-serif;font-size:8px;}" in svg
    )
    assert ">GND<" in svg
    assert 'class="prea-gnd-pin"' in svg
    assert 'class="prea-fixed-bbox"' in svg
    assert "<title>IC1</title>" in svg
    assert 'class="prea-fixed-pad"' in svg
    c1_pad = re.search(
        r'<polygon class="prea-rlc-pad" points="([^"]+)"><title>C1\.PIN_1</title></polygon>',
        svg,
    )
    r1_pad = re.search(
        r'<polygon class="prea-rlc-pad" points="([^"]+)"><title>R1\.PIN_1</title></polygon>',
        svg,
    )
    assert c1_pad is not None and r1_pad is not None
    assert c1_pad.group(1) != r1_pad.group(1)
    assert "<title>C3.PIN_1</title>" in svg
    assert "<title>C3.PIN_2</title>" in svg
    assert "<title>C5.PIN_1</title>" in svg
    assert "<title>C5.PIN_2</title>" in svg
    assert "<title>C6.PIN_1</title>" in svg
    assert "<title>C6.PIN_2</title>" in svg
    for comp in ("C3", "C5", "C6"):
        pin1_match = re.search(
            rf'<polygon class="prea-rlc-pad" points="([^"]+)"><title>{comp}\.PIN_1</title></polygon>',
            svg,
        )
        pin2_match = re.search(
            rf'<polygon class="prea-rlc-pad" points="([^"]+)"><title>{comp}\.PIN_2</title></polygon>',
            svg,
        )
        assert pin1_match is not None and pin2_match is not None
        p1 = [
            tuple(map(float, point.split(","))) for point in pin1_match.group(1).split()
        ]
        p2 = [
            tuple(map(float, point.split(","))) for point in pin2_match.group(1).split()
        ]
        p1_center = (sum(x for x, _ in p1) / 4.0, sum(y for _, y in p1) / 4.0)
        p2_center = (sum(x for x, _ in p2) / 4.0, sum(y for _, y in p2) / 4.0)
        # Follow the same left/front/right rule family as R3/C4: avoid diagonal
        # pin-to-pin placement for shunt capacitors.
        assert (
            min(abs(p2_center[0] - p1_center[0]), abs(p2_center[1] - p1_center[1]))
            < 0.35
        )

    pre_a_json = scratch_dir / "PA_Module_Simplified.preA.json"
    payload = json.loads(pre_a_json.read_text(encoding="utf-8"))
    viewer_json = scratch_dir / "PA_Module_Simplified.viewer.json"
    viewer_payload = json.loads(viewer_json.read_text(encoding="utf-8"))
    assert "yaml_geometric_constraints_applied" in payload
    assert any(
        item.get("edge_id") == "IC1_pin1_seg3"
        and item.get("mode") == "strict_junction_rule"
        for item in payload["yaml_geometric_constraints_applied"]
    )

    by_edge = {edge["edge_id"]: edge for edge in payload["edges"]}
    viewer_by_edge = {
        edge["edge_id"]: edge for edge in viewer_payload["phases"]["preA"]["edges"]
    }
    assert {
        "IC1_pin1_seg3",
        "IC1_pin1_seg4",
        "IC1_pin2_seg2",
        "IC1_pin2_seg3",
    }.issubset(viewer_by_edge)
    for edge_id in ("IC1_pin1_seg3", "IC1_pin1_seg4", "IC1_pin2_seg2", "IC1_pin2_seg3"):
        assert (
            by_edge[edge_id]["render_endpoint_positions_mm"]
            == viewer_by_edge[edge_id]["endpoint_positions_mm"]
        )
    assert {
        placement["ref"]
        for placement in viewer_payload["phases"]["preA"]["uv_placements"]
    } >= {"C1", "R1", "C3", "C5", "C6"}
    # IC1_pin2_seg2 → C3.PIN_1, seg3 → C6.PIN_2 (no virtual stubs).
    # Old test checked y-separation > 1.5mm for junction nodes; now RLC PINs
    # are placed by UV adhesion and may be closer. Just verify non-overlap.
    pin2_seg2 = by_edge["IC1_pin2_seg2"]["endpoint_positions_mm"]["C3.PIN_1"]
    pin2_seg3 = by_edge["IC1_pin2_seg3"]["endpoint_positions_mm"]["C6.PIN_2"]
    assert abs(pin2_seg2["y"] - pin2_seg3["y"]) > 0.1  # non-overlap check

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

    # IC1 PIN_1 seg3 branch should be interpreted relative to seg1 direction.
    seg3_render = by_edge["IC1_pin1_seg3"]["render_endpoint_positions_mm"]
    seg3_start = seg3_render["IC1_pin1_seg1_universal_node"]
    seg3_end = seg3_render["C5.PIN_1"]  # terminates at C5.PIN_1 (no virtual stub)
    seg1_w = float(by_edge["IC1_pin1_seg1"]["width"])
    # offset_v=edge_left and offset_u=-1.6 on a downward reference edge:
    # launch point shifts +x (left edge) and +y (back from front edge).
    assert seg3_start["x"] - pin1_node["x"] == pytest.approx(seg1_w / 2.0, abs=0.15)
    assert seg3_start["y"] - pin1_node["y"] == pytest.approx(1.6, abs=0.2)
    # angle=90 means seg3 runs to the right from its launch point.
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

    c1r1_from_seg4 = by_edge["IC1_pin1_seg4"]["render_endpoint_positions_mm"][
        "C1.PIN_1,R1.PIN_1"
    ]
    seg5_render = by_edge["IC1_pin1_seg5"]["render_endpoint_positions_mm"]
    c1r1_pin2 = seg5_render["C1.PIN_2,R1.PIN_2"]
    assert c1r1_pin2["x"] == pytest.approx(c1r1_from_seg4["x"], abs=0.15)
    assert c1r1_pin2["y"] < c1r1_from_seg4["y"]
    seg5_len = math.hypot(
        seg5_render["C2.PIN_1"]["x"] - c1r1_pin2["x"],
        seg5_render["C2.PIN_1"]["y"] - c1r1_pin2["y"],
    )
    assert seg5_len == pytest.approx(25.33, abs=0.8)
    pin2_seg5 = by_edge["IC1_pin2_seg5"]["render_endpoint_positions_mm"]
    pin2_seg5_start = pin2_seg5["R3.PIN_2"]
    c4_pin1 = pin2_seg5["C4.PIN_1"]
    assert abs(c4_pin1["y"] - pin2_seg5_start["y"]) > 1.0
    pin1_seg4 = by_edge["IC1_pin1_seg4"]["render_endpoint_positions_mm"]
    pin2_seg6 = by_edge["IC1_pin2_seg6"]["render_endpoint_positions_mm"]
    seg4_a = pin1_seg4["IC1_pin1_seg1_universal_node"]
    seg4_b = pin1_seg4["C1.PIN_1,R1.PIN_1"]
    seg6_a = pin2_seg6["C4.PIN_2"]
    seg6_b = pin2_seg6["TP5.PIN_1"]

    def _orientation(
        a: dict[str, float], b: dict[str, float], c: dict[str, float]
    ) -> float:
        return (b["x"] - a["x"]) * (c["y"] - a["y"]) - (b["y"] - a["y"]) * (
            c["x"] - a["x"]
        )

    seg4_to_seg6_cross = (
        _orientation(seg4_a, seg4_b, seg6_a) * _orientation(seg4_a, seg4_b, seg6_b)
        < 0.0
        and _orientation(seg6_a, seg6_b, seg4_a) * _orientation(seg6_a, seg6_b, seg4_b)
        < 0.0
    )
    assert not seg4_to_seg6_cross
    assert 'class="prea-edge-bridge"' in svg
    assert 'class="prea-edge-bridge" data-edge-id="RF_INPUT_to_IC1"' in svg
    assert 'class="prea-edge-bridge" data-edge-id="PWR_VDD_bus"' in svg
    rf_input_match = re.search(
        r'data-edge-id="RF_INPUT_to_IC1"[^>]*x1="([0-9.]+)"[^>]*x2="([0-9.]+)"', svg
    )
    assert rf_input_match is not None
    rf_x1, rf_x2 = (float(v) for v in rf_input_match.groups())
    assert abs(rf_x1 - rf_x2) < 0.6
    assert 'data-endpoint="C1.PIN_1"' not in svg
    assert 'data-endpoint="C1.PIN_2"' not in svg
    assert 'data-endpoint="R1.PIN_1"' not in svg
    assert 'data-endpoint="R1.PIN_2"' not in svg

    phase_a_svg = scratch_dir / "PA_Module_Simplified.phaseA.svg"
    phase_a_text = phase_a_svg.read_text(encoding="utf-8")
    assert 'stroke-linecap="butt"' in phase_a_text
    assert 'stroke-linecap="round"' not in phase_a_text

    phase_b_svg = scratch_dir / "PA_Module_Simplified.phaseB.svg"
    phase_b_text = phase_b_svg.read_text(encoding="utf-8")
    assert 'class="component-bbox"' in phase_b_text
    assert "<title>C1.PIN_1</title>" in phase_b_text


def test_render_pre_phase_svg_zero_length_edge_no_crash(
    scratch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = FrontendArtifact(
        project_name="zero_len",
        board={"width": 20.0, "height": 20.0},
        lint_report=LintReport(),
        fixed_terminals={
            "A.P1": ExpandedPad(
                component="A",
                pin="P1",
                abs_x=5.0,
                abs_y=5.0,
                orientation=0.0,
                kind="fixed",
            )
        },
        edges={
            "E0": TriagedEdge(
                name="E0",
                edge_type="microstrip",
                routing_class="rf_constrained",
                target_length=4.0,
                width=1.0,
                connections=("A.P1", "N1"),
            )
        },
        nodes={
            "N1": NormalizedNode(
                name="N1",
                original_type="junction",
                normalized_type="junction",
            )
        },
    )
    fake_result = SimpleNamespace(artifact=artifact)

    def _fake_topology(*_args, **_kwargs):
        return pcb_solve_v2.PreATopologyModel(
            endpoint_positions={"A.P1": (5.0, 5.0), "N1": (5.0, 5.0)},
            edge_endpoint_overrides={},
            edges=[],
            uv_placements=[],
        )

    monkeypatch.setattr(pcb_solve_v2, "_build_prea_topology_model", _fake_topology)
    svg_path = scratch_dir / "zero_len.preA.svg"

    positions, virtual_endpoints, overrides = pcb_solve_v2._render_pre_phase_svg(
        fake_result, svg_path
    )

    assert svg_path.exists()
    assert "A.P1" in positions and "N1" in positions
    assert virtual_endpoints == set()
    assert overrides == {}
