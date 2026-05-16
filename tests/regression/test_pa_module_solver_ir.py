"""Regression test: full PA_Module_Simplified compiles to valid SolverIR."""

from __future__ import annotations

from pathlib import Path

from frontend.solver_ir import compile_solver_ir, host_match_summary

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "rf_layout_simplified.yaml"


def test_pa_module_solver_ir_full_unique() -> None:
    ir = compile_solver_ir(YAML_PATH)
    assert ir.project == "PA_Module_Simplified"
    summary = host_match_summary(ir)
    # R2 removed; pin1_seg2+C7 added → 7 unique
    assert summary["unique"] == 7
    assert summary["ambiguous"] == 0
    assert summary["missing"] == 2


def test_pa_module_specific_uv_hosts() -> None:
    ir = compile_solver_ir(YAML_PATH)
    # C6 has anchor_pin=PIN_2, reference_net=RF_NET_2.
    assert ir.uv_resolutions["C6"].anchor_pin == "PIN_2"
    assert ir.uv_resolutions["C6"].reference_net == "RF_NET_2"
    assert not ir.uv_resolutions["C6"].synthetic_host


def test_pa_module_universal_junctions() -> None:
    ir = compile_solver_ir(YAML_PATH)
    assert set(ir.junction_templates.keys()) == {
        "IC1_pin1_seg1_universal_node",
        "IC1_pin2_seg1_universal_node",
    }
    total_branches = sum(len(t.branches) for t in ir.junction_templates.values())
    # pin1 node has 3 branches (seg2, seg3, seg4); pin2 node has 3 (seg2, seg3, seg4)
    assert total_branches == 6

    # Sanity-check axis-aligned trig: angle=0/90/-90 produce exact integer cos/sin.
    for tpl in ir.junction_templates.values():
        for branch in tpl.branches:
            if branch.angle_deg == 0.0:
                assert branch.dx == branch.offset_u
                assert branch.dy == branch.signed_v
            elif branch.angle_deg == 90.0:
                assert branch.dx == -branch.signed_v
                assert branch.dy == branch.offset_u
            elif branch.angle_deg == -90.0:
                assert branch.dx == branch.signed_v
                assert branch.dy == -branch.offset_u


def test_pa_module_edges_classified() -> None:
    ir = compile_solver_ir(YAML_PATH)
    locked = sum(
        1 for e in ir.edges.values() if e.routing_class.value == "rf_constrained_locked"
    )
    free = sum(
        1 for e in ir.edges.values() if e.routing_class.value == "rf_constrained_free"
    )
    flex = sum(1 for e in ir.edges.values() if e.routing_class.value == "flexible_path")
    assert locked + free + flex == len(ir.edges)
    assert locked >= 1
