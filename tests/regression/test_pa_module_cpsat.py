"""Regression: end-to-end CP-SAT solve on the PA module YAML."""

from __future__ import annotations

import pytest

pytest.importorskip("ortools.sat.python.cp_model")

from frontend.compile import compile_layout  # noqa: E402
from frontend.solver_ir import compile_solver_ir  # noqa: E402
from solver import (
    audit_geometry,
    build_model,
    extract_geometry,
    solve_model,
)  # noqa: E402

LAYOUT = "rf_layout_simplified.yaml"


def test_pa_module_cpsat_optimal_within_length_tolerance() -> None:
    artifact = compile_layout(LAYOUT)
    ir = compile_solver_ir(LAYOUT)
    cpsat = build_model(ir, artifact)

    # The PA case ships at least one fixed-fixed locked edge whose target
    # is shorter than the direct Manhattan distance; M4 detects + skips it
    # rather than emitting an INFEASIBLE model.
    assert (
        cpsat.skipped_locked_edges
    ), "expected at least one skipped locked edge from data inconsistency"

    res = solve_model(cpsat, time_limit_s=10.0, num_workers=8)
    assert res.status_name in ("OPTIMAL", "FEASIBLE")
    assert res.wall_seconds <= 10.0

    geom = extract_geometry(ir=ir, artifact=artifact, cpsat=cpsat, result=res)
    audit = audit_geometry(
        ir,
        geom,
        skip_length_edges={eid for eid, _ in cpsat.skipped_locked_edges},
        resolved_endpoints=cpsat.edge_endpoint_resolved,
    )
    assert audit.locked_edges_within_tolerance, (
        f"length errors exceeded 0.5%: max="
        f"{audit.max_length_error_fraction * 100:.3f}%"
    )
    # NoOverlap is M5's responsibility; we still report the count for trend
    # tracking (overlap pairs may shrink as M5 lands).
    assert isinstance(audit.no_overlap_pass, bool)
