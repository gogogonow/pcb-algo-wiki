"""场景化 PreA 流水线 (:mod:`solver.v2.prea_pipeline`) 单元测试.

使用 ``rf_layout_simplified.yaml`` 真实案例验证：

* IC 固定 pin 的微带线树端点位置由 pin orientation/长度严格推导
* 串联 RLC（C1/R1）的两 pin 端点都有坐标
* 场景 2 shunt UV（C5）端点 fallback 到 plan_xy 不缺失
* 复合端点拆分后每个成员都有相同坐标
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from frontend.compile import compile_layout
from solver.v2.node_planner import plan_node_positions
from solver.v2.prea_pipeline import (
    prelayout_floating_devices,
    solve_prea_scene_split,
    sync_composite_endpoints,
)
from tools.pcb_solve_v2 import (
    _apply_junction_templates,
    _load_branch_offset_u_tokens,
    _load_junction_templates,
    _propagate_constrained_edges,
    _seed_fixed_positions,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_CASE = REPO_ROOT / "rf_layout_simplified.yaml"


@pytest.fixture(scope="module")
def real_case_inputs():
    artifact = compile_layout(REAL_CASE)
    board_w = float(artifact.board["width"])
    board_h = float(artifact.board["height"])
    plan = plan_node_positions(
        artifact, board_width_mm=board_w, board_height_mm=board_h
    )
    templates = _load_junction_templates(layout_path=REAL_CASE)
    offsets = _load_branch_offset_u_tokens(layout_path=REAL_CASE)
    return artifact, dict(plan.endpoint_xy), board_w, board_h, templates, offsets


def _run_pipeline(real_case_inputs):
    artifact, plan_xy, bw, bh, templates, offsets = real_case_inputs
    positions, overrides = solve_prea_scene_split(
        artifact,
        plan_xy,
        board_w=bw,
        board_h=bh,
        junction_templates=templates,
        branch_offset_u_tokens=offsets,
        seed_fixed_positions=_seed_fixed_positions,
        apply_junction_templates=_apply_junction_templates,
        propagate_constrained_edges=_propagate_constrained_edges,
    )
    for ep, xy in plan_xy.items():
        positions.setdefault(ep, (float(xy[0]), float(xy[1])))
    return positions, overrides


def test_fixed_ic_pins_keep_absolute_positions(real_case_inputs):
    artifact, *_ = real_case_inputs
    positions, _ = _run_pipeline(real_case_inputs)
    for ep, pad in artifact.fixed_terminals.items():
        if pad.abs_x is None or pad.abs_y is None:
            continue
        assert positions[ep] == pytest.approx((pad.abs_x, pad.abs_y))


def test_ic_pin_first_segment_distance_matches_target_length(real_case_inputs):
    artifact, *_ = real_case_inputs
    positions, _ = _run_pipeline(real_case_inputs)
    edge = artifact.edges["IC1_pin1_seg1"]
    target = float(edge.target_length)
    a, b = edge.connections
    pa, pb = positions[a], positions[b]
    d = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
    assert d == pytest.approx(
        target, rel=0.05
    ), f"seg1 length {d:.3f} should be near target {target:.3f}"


def test_series_rlc_pins_have_positions(real_case_inputs):
    positions, _ = _run_pipeline(real_case_inputs)
    # 串联 RLC：C1/R1 两个 pin、C2 两个 pin
    for pin in ("C1.PIN_1", "C1.PIN_2", "R1.PIN_1", "R1.PIN_2", "C2.PIN_1", "C2.PIN_2"):
        assert pin in positions, f"series RLC pin {pin} missing"


def test_shunt_uv_pins_fall_back_to_plan_xy(real_case_inputs):
    _, plan_xy, *_ = real_case_inputs
    positions, _ = _run_pipeline(real_case_inputs)
    # C5 是 shunt UV：场景 2 在 PreA 不输出位置，但外层用 plan_xy 兜底
    for pin in ("C5.PIN_1", "C5.PIN_2"):
        assert pin in positions, f"shunt UV pin {pin} should be filled by fallback"


def test_composite_endpoint_members_share_coordinate(real_case_inputs):
    positions, _ = _run_pipeline(real_case_inputs)
    composite = "C1.PIN_1,R1.PIN_1"
    if composite in positions:
        assert positions["C1.PIN_1"] == positions[composite]
        assert positions["R1.PIN_1"] == positions[composite]


def test_pipeline_is_deterministic(real_case_inputs):
    positions1, _ = _run_pipeline(real_case_inputs)
    positions2, _ = _run_pipeline(real_case_inputs)
    assert positions1 == positions2


def test_prelayout_floating_outward_extends_along_unit_vector():
    """场景 3 极简预布局：从一个 constrained 端点向外推一个未定位邻居."""

    class _Edge:
        edge_type = "microstrip"
        connections = ("A", "B")
        target_length = 5.0

    class _Art:
        edges = {"e": _Edge()}
        components: dict = {}
        uv_components: dict = {}

    positions = {"A": (10.0, 10.0)}
    constrained = {"A"}
    prelayout_floating_devices(
        _Art(),
        positions,
        constrained,
        scene3_edges={"e"},
        board_w=100.0,
        board_h=100.0,
    )
    assert "B" in positions
    # 默认水平 +x 方向，距离 5.0
    assert positions["B"] == pytest.approx((15.0, 10.0))


def test_sync_composite_endpoints_fills_member_tokens():
    positions = {"X.PIN_1,Y.PIN_1": (3.0, 4.0)}
    sync_composite_endpoints(positions)
    assert positions["X.PIN_1"] == (3.0, 4.0)
    assert positions["Y.PIN_1"] == (3.0, 4.0)
