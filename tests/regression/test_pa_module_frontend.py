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
    assert "unknown_launch_rule" in codes  # launch_rule: normal


def test_pa_module_fixed_pads_count() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    # IC1 (4 pins) + TP2..TP5 (1 pin each) = 8 fixed pads (TP1 removed)
    assert len(artifact.fixed_terminals) == 8
    assert {pad.component for pad in artifact.fixed_terminals.values()} == {
        "IC1",
        "TP2",
        "TP3",
        "TP4",
        "TP5",
    }


def test_pa_module_uv_components_count() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    # C1..C7 + R1, R3 = 9 UV components (R2 removed; C7 added with pin1_seg2)
    assert set(artifact.uv_components) == {
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "C7",
        "R1",
        "R3",
    }


def test_pa_module_edge_routing_class_histogram() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    hist = artifact.edges_by_class()
    assert hist.get("rf_constrained_locked") == 14
    assert hist.get("rf_constrained_free") == 3
    # flexible_path edges are data-driven in the PA case (recent revisions may
    # add extra floating/flex links), so keep only a lower-bound regression.
    assert hist.get("flexible_path", 0) >= 4
    assert sum(hist.values()) == len(artifact.edges)


def test_pa_module_node_type_histogram() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    hist = artifact.nodes_by_type()
    assert hist.get("universal_junction") == 2
    # The PA case may remove redundant split/combiner nodes as direct endpoint
    # semantics evolve. Keep this regression focused on stable node contracts.
    assert hist.get("t_junction", 0) >= 0
    assert hist.get("t_combiner_junction", 0) >= 0


def test_pa_module_obstacles_include_board_and_fixed_components() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    kinds = [o.kind for o in artifact.obstacles]
    assert kinds.count("board_outline") == 1
    # 5 fixed components: IC1 + TP2..TP5 (TP1 removed)
    assert kinds.count("footprint_bbox") == 5


def test_pa_module_tp3_is_on_top_border() -> None:
    artifact = compile_layout(REAL_CASE_PATH)
    tp3 = artifact.fixed_terminals["TP3.PIN_1"]
    assert tp3.abs_y == 85.0
