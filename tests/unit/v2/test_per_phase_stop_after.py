"""Tests for the per-phase ``--stop-after`` CLI feature.

Covers:
* ``compile_and_plan`` returns a usable artifact + plan.
* ``run_phase_a`` returns a PhaseAResult with a skeleton.
* ``run_phase_b`` returns a PhaseBResult + updated skeleton.
* ``run_phase_c`` returns PhaseCResult + GeometryIR.
* ``_make_partial_result`` builds a valid OrchestratorV2Result for each stop point.
* CLI argument ``--stop-after`` is accepted by the parser and routes to the
  correct exit path (preA / phaseA / phaseB / phaseC).
"""

from __future__ import annotations

from pathlib import Path

import pytest

YAML_PATH = Path(__file__).resolve().parents[3] / "rf_layout_simplified.yaml"
pytestmark = pytest.mark.skipif(not YAML_PATH.exists(), reason="PA yaml missing")


# ---------------------------------------------------------------------------
# orchestrator per-phase API tests
# ---------------------------------------------------------------------------


def test_compile_and_plan_returns_artifact_and_plan() -> None:
    from solver.v2 import OrchestratorV2Options, compile_and_plan
    from frontend.compile import FrontendArtifact
    from solver.v2.node_planner import NodePlan

    artifact, plan = compile_and_plan(
        YAML_PATH, OrchestratorV2Options(grid_step_um=200)
    )

    assert isinstance(artifact, FrontendArtifact)
    assert isinstance(plan, NodePlan)
    assert len(artifact.edges) > 0
    assert len(plan.endpoint_xy) > 0


def test_run_phase_a_returns_phase_a_result() -> None:
    from solver.v2 import (
        OrchestratorV2Options,
        compile_and_plan,
        run_phase_a,
        PhaseAResult,
    )

    opts = OrchestratorV2Options(grid_step_um=200, rip_up_rounds=1)
    artifact, plan = compile_and_plan(YAML_PATH, opts)
    phase_a = run_phase_a(artifact, plan, YAML_PATH, opts)

    assert isinstance(phase_a, PhaseAResult)
    assert phase_a.wall_seconds > 0.0
    ok, total = phase_a.skeleton.success_rate()
    assert total > 0  # at least attempted some routes


def test_run_phase_b_returns_result_and_skeleton() -> None:
    from solver.v2 import (
        OrchestratorV2Options,
        compile_and_plan,
        run_phase_a,
        run_phase_b,
        PhaseBResult,
        SkeletonReport,
    )

    opts = OrchestratorV2Options(grid_step_um=200, rip_up_rounds=1)
    artifact, plan = compile_and_plan(YAML_PATH, opts)
    phase_a = run_phase_a(artifact, plan, YAML_PATH, opts)
    phase_b, skeleton = run_phase_b(artifact, plan, phase_a, opts)

    assert isinstance(phase_b, PhaseBResult)
    assert isinstance(skeleton, SkeletonReport)
    assert phase_b.wall_seconds >= 0.0


def test_run_phase_c_returns_result_and_geometry() -> None:
    from solver.v2 import (
        OrchestratorV2Options,
        compile_and_plan,
        run_phase_a,
        run_phase_b,
        run_phase_c,
        PhaseCResult,
    )
    from schema.geometry_ir import GeometryIR

    opts = OrchestratorV2Options(grid_step_um=200, rip_up_rounds=1)
    artifact, plan = compile_and_plan(YAML_PATH, opts)
    phase_a = run_phase_a(artifact, plan, YAML_PATH, opts)
    phase_b, skeleton = run_phase_b(artifact, plan, phase_a, opts)
    phase_c, geometry = run_phase_c(
        YAML_PATH, artifact, plan, skeleton, phase_a, phase_b, opts
    )

    assert isinstance(phase_c, PhaseCResult)
    assert isinstance(geometry, GeometryIR)
    assert geometry.project != ""


def test_make_partial_result_prea_stub() -> None:
    """_make_partial_result with no phases builds a valid OrchestratorV2Result."""
    from solver.v2 import compile_and_plan, OrchestratorV2Options, OrchestratorV2Result
    from tools.pcb_solve_v2 import _make_partial_result

    artifact, plan = compile_and_plan(
        YAML_PATH, OrchestratorV2Options(grid_step_um=200)
    )
    result = _make_partial_result(artifact, plan)

    assert isinstance(result, OrchestratorV2Result)
    assert result.geometry is not None
    assert result.phase_a.skeleton is not None
    # No routing was done — success count must be zero.
    ok, total = result.phase_a.skeleton.success_rate()
    assert ok == 0


def test_make_partial_result_phase_a_stub() -> None:
    """_make_partial_result with phase_a fills in empty B+C stubs."""
    from solver.v2 import compile_and_plan, run_phase_a, OrchestratorV2Options
    from tools.pcb_solve_v2 import _make_partial_result

    opts = OrchestratorV2Options(grid_step_um=200, rip_up_rounds=1)
    artifact, plan = compile_and_plan(YAML_PATH, opts)
    phase_a = run_phase_a(artifact, plan, YAML_PATH, opts)
    result = _make_partial_result(artifact, plan, phase_a=phase_a)

    # Phase A skeleton is present; phase B adhesion is empty.
    assert result.phase_a.wall_seconds == phase_a.wall_seconds
    assert len(result.phase_b.adhesion.placements) == 0
    assert len(result.phase_c.routed_flex_edges) == 0


# ---------------------------------------------------------------------------
# CLI argument parser tests
# ---------------------------------------------------------------------------


def test_build_parser_has_stop_after_arg() -> None:
    from tools.pcb_solve_v2 import _build_parser

    parser = _build_parser()
    # default
    ns = parser.parse_args(["dummy.yaml"])
    assert ns.stop_after == "phaseC"


def test_build_parser_stop_after_choices() -> None:
    from tools.pcb_solve_v2 import _build_parser

    parser = _build_parser()
    for choice in ("preA", "phaseA", "phaseB", "phaseC"):
        ns = parser.parse_args(["dummy.yaml", "--stop-after", choice])
        assert ns.stop_after == choice


def test_build_parser_stop_after_rejects_invalid() -> None:
    import pytest
    from tools.pcb_solve_v2 import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dummy.yaml", "--stop-after", "phaseD"])


# ---------------------------------------------------------------------------
# CLI main() smoke tests for --stop-after
# ---------------------------------------------------------------------------


def test_cli_stop_after_prea_exits_zero(tmp_path: Path) -> None:
    from tools.pcb_solve_v2 import main

    rc = main(
        [str(YAML_PATH), "--out-dir", str(tmp_path), "--stop-after", "preA", "--quiet"]
    )
    assert rc == 0
    # preA artefacts should exist
    preA_files = list(tmp_path.glob("*.preA.*"))
    assert len(preA_files) >= 1


def test_cli_stop_after_phasea_emits_artefacts(tmp_path: Path) -> None:
    from tools.pcb_solve_v2 import main

    rc = main(
        [
            str(YAML_PATH),
            "--out-dir",
            str(tmp_path),
            "--stop-after",
            "phaseA",
            "--grid-step-um",
            "200",
            "--rip-up-rounds",
            "1",
            "--quiet",
        ]
    )
    assert rc in (0, 2)  # 2 if no routes succeeded (unlikely but allowed)
    # phaseA JSON should be written
    phaseA_files = list(tmp_path.glob("*.phaseA.json"))
    assert len(phaseA_files) >= 1


def test_cli_stop_after_phaseb_emits_artefacts(tmp_path: Path) -> None:
    from tools.pcb_solve_v2 import main

    rc = main(
        [
            str(YAML_PATH),
            "--out-dir",
            str(tmp_path),
            "--stop-after",
            "phaseB",
            "--grid-step-um",
            "200",
            "--rip-up-rounds",
            "1",
            "--quiet",
        ]
    )
    assert rc in (0, 2)
    phaseB_files = list(tmp_path.glob("*.phaseB.json"))
    assert len(phaseB_files) >= 1
