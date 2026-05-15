from pathlib import Path

from frontend import compile_layout

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CASE_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_pa_module_lint_repairs_known_typos() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    befores = sorted(r.before for r in artifact.lint_report.repairs)
    # PKG_TEST_POINT footprint contains 2 ``redius`` and 1 ``cicle`` typos
    assert befores == ["cicle", "redius", "redius"]


def test_pa_module_lint_warns_unknown_enums() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    codes = {w.code for w in artifact.lint_report.warnings}
    assert "unknown_bend_style" in codes  # bend_style: curved
    assert "unknown_launch_rule" in codes  # launch_rule: normal


def test_pa_module_fixed_pads_count() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    # IC1 (4 pins) + TP1..TP5 (1 pin each) = 9 fixed pads
    assert len(artifact.fixed_terminals) == 9
    assert {pad.component for pad in artifact.fixed_terminals.values()} == {
        "IC1",
        "TP1",
        "TP2",
        "TP3",
        "TP4",
        "TP5",
    }


def test_pa_module_uv_components_count() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    # C1..C6 + R1..R3 = 9 UV components
    assert set(artifact.uv_components) == {
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "R1",
        "R2",
        "R3",
    }


def test_pa_module_edge_routing_class_histogram() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    hist = artifact.edges_by_class()
    # ITERATION-PLAN.md §3 estimated 14 locked / 8 free; actual data is
    # 15 locked / 7 free (one extra rf_constrained edge carries a target_length).
    assert hist.get("rf_constrained_locked") == 15
    assert hist.get("rf_constrained_free") == 7
    assert hist.get("flexible_path", 0) == 0
    assert sum(hist.values()) == 22


def test_pa_module_node_type_histogram() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    hist = artifact.nodes_by_type()
    assert hist.get("universal_junction") == 2
    assert hist.get("t_junction") == 4
    assert hist.get("t_combiner_junction") == 2


def test_pa_module_obstacles_include_board_and_fixed_components() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    kinds = [o.kind for o in artifact.obstacles]
    assert kinds.count("board_outline") == 1
    # 6 fixed components: IC1 + TP1..TP5
    assert kinds.count("footprint_bbox") == 6


def test_pa_module_tp3_is_on_top_border() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    tp3 = artifact.fixed_terminals["TP3.PIN_1"]
    assert tp3.abs_y == 100.0
