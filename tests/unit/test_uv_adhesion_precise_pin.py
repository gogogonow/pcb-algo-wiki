"""
测试 uv_adhesion 的精确pin定位功能（WI-I）
"""

from frontend.models import FrontendArtifact, TriagedEdge
from solver.v2.uv_adhesion import _is_pin_microstrip_endpoint
from solver.v2.skeleton_router import SkeletonReport


def test_is_pin_microstrip_endpoint_true():
    """测试：pin 是主微带线的终点"""
    # 构造 SkeletonReport
    skel = SkeletonReport(
        routes={},
        final_endpoint_um={"C7.PIN_1": (18000, 27800)},
    )

    # 构造 FrontendArtifact with edges
    artifact = FrontendArtifact(
        project_name="test",
        board={"width": 40.0, "height": 100.0},
        lint_report={"errors": [], "repairs": []},
        edges={
            "seg2": TriagedEdge(
                name="seg2",
                edge_type="microstrip",
                routing_class="rf_constrained",
                connections=("IC1_pin1_seg1_universal_node", "C7.PIN_1"),
                width=1.88,
                target_length=1.29,
                net="RF_NET_1",
            )
        },
    )

    result = _is_pin_microstrip_endpoint(
        pin_id="C7.PIN_1",
        part_id="C7",
        anchor_pin_name="PIN_1",
        skel=skel,
        artifact=artifact,
    )

    assert result is True, "应识别为微带线终点"


def test_is_pin_microstrip_endpoint_false_no_constraint():
    """测试：pin 连接的是虚拟stub（无约束），不是主微带线"""
    skel = SkeletonReport(
        routes={},
        final_endpoint_um={"C7.PIN_1": (18000, 27800)},
    )

    artifact = FrontendArtifact(
        project_name="test",
        board={"width": 40.0, "height": 100.0},
        lint_report={"errors": [], "repairs": []},
        edges={
            "stub_to_C7": TriagedEdge(
                name="stub_to_C7",
                edge_type="microstrip",
                routing_class="rf_constrained",
                connections=("some_pad", "C7.PIN_1"),
                width=None,  # 无约束 = 虚拟stub
                target_length=None,
                net="RF_NET_1",
            )
        },
    )

    result = _is_pin_microstrip_endpoint(
        pin_id="C7.PIN_1",
        part_id="C7",
        anchor_pin_name="PIN_1",
        skel=skel,
        artifact=artifact,
    )

    assert result is False, "虚拟stub不应触发精确定位"


def test_is_pin_microstrip_endpoint_false_not_in_final_endpoint():
    """测试：pin 不在 final_endpoint_um 中"""
    skel = SkeletonReport(routes={}, final_endpoint_um={})

    artifact = FrontendArtifact(
        project_name="test",
        board={"width": 40.0, "height": 100.0},
        lint_report={"errors": [], "repairs": []},
        edges={
            "seg2": TriagedEdge(
                name="seg2",
                edge_type="microstrip",
                routing_class="rf_constrained",
                connections=("node1", "C7.PIN_1"),
                width=1.88,
                target_length=None,
                net="RF_NET_1",
            )
        },
    )

    result = _is_pin_microstrip_endpoint(
        pin_id="C7.PIN_1",
        part_id="C7",
        anchor_pin_name="PIN_1",
        skel=skel,
        artifact=artifact,
    )

    assert result is False, "不在 final_endpoint_um 中不应触发"
