"""PreA 场景分类器单元测试."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.compile import compile_layout
from solver.v2.prea_scenes import (
    SCENE_1_FIXED_TREE,
    SCENE_2_SHUNT_UV,
    SCENE_3_FLOATING,
    SCENE_NOT_APPLICABLE,
    classify_edges,
    scene_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_CASE = REPO_ROOT / "rf_layout_simplified.yaml"


@pytest.fixture(scope="module")
def real_artifact():
    return compile_layout(REAL_CASE)


def test_classify_edges_returns_one_entry_per_edge(real_artifact):
    classifications = classify_edges(real_artifact)
    assert set(classifications.keys()) == set(real_artifact.edges.keys())
    for name, c in classifications.items():
        assert c.edge_name == name
        assert c.scene in {
            SCENE_1_FIXED_TREE,
            SCENE_2_SHUNT_UV,
            SCENE_3_FLOATING,
            SCENE_NOT_APPLICABLE,
        }


def test_flexible_path_edges_classified_not_applicable(real_artifact):
    classifications = classify_edges(real_artifact)
    flex_names = [
        n for n, e in real_artifact.edges.items() if e.edge_type != "microstrip"
    ]
    assert flex_names, "case must include at least one non-microstrip edge"
    for name in flex_names:
        assert classifications[name].scene == SCENE_NOT_APPLICABLE


def test_ic_pin_first_segment_is_scene_1(real_artifact):
    classifications = classify_edges(real_artifact)
    # 第一段 microstrip 从 IC1.PIN_1 出发，必定是 scene 1.
    edge_name = "IC1_pin1_seg1"
    assert edge_name in classifications, f"expected {edge_name} in real case"
    assert classifications[edge_name].scene == SCENE_1_FIXED_TREE


def test_shunt_cap_classified_as_scene_2(real_artifact):
    """C5 是 PIN_1 → microstrip split_pad、PIN_2 → GND 的 shunt cap."""
    classifications = classify_edges(real_artifact)
    # 找到任一连接 C5 的 microstrip 边
    c5_edges = [
        n
        for n, e in real_artifact.edges.items()
        if e.edge_type == "microstrip" and any("C5." in conn for conn in e.connections)
    ]
    if not c5_edges:
        pytest.skip("C5 microstrip edge not in current case fixture")
    for name in c5_edges:
        assert (
            classifications[name].scene == SCENE_2_SHUNT_UV
        ), f"{name} expected SCENE_2 but got {classifications[name]}"


def test_scene_summary_counts_match(real_artifact):
    classifications = classify_edges(real_artifact)
    summary = scene_summary(classifications)
    assert sum(summary.values()) == len(classifications)


def test_classify_empty_artifact_returns_empty():
    class _Empty:
        edges: dict = {}
        fixed_terminals: dict = {}
        uv_components: dict = {}
        nodes: dict = {}

    assert classify_edges(_Empty()) == {}
