"""Micro-tests for ``solver.cpsat.build_model`` and ``solve_model``.

Uses a hand-crafted minimal SolverIR with two fixed pads connected by one
locked edge — verifies the solver finds a Manhattan-feasible length.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ortools.sat.python.cp_model")

from frontend.models import (  # noqa: E402
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
)
from schema.solver_ir import (  # noqa: E402
    RoutingClass,
    SolverEdge,
    SolverIR,
)
from schema.v6_ir import Board, Point, V6Terminal  # noqa: E402
from solver import (
    audit_geometry,
    build_model,
    extract_geometry,
    solve_model,
)  # noqa: E402


def _make_micro_case(target_length: float = 4.0) -> tuple[SolverIR, FrontendArtifact]:
    board = Board(origin=Point(x=0.0, y=0.0), width=20.0, height=20.0)
    edges = {
        "e1": SolverEdge(
            endpoints=("A.PIN_1", "B.PIN_1"),
            routing_class=RoutingClass.RF_CONSTRAINED_LOCKED,
            target_length=target_length,
            width=0.5,
        ),
    }
    terminals = {
        "A.PIN_1": V6Terminal(point=Point(x=2.0, y=5.0)),
        "B.PIN_1": V6Terminal(point=Point(x=2.0, y=9.0)),
    }
    ir = SolverIR(
        project="micro",
        board=board,
        clearance=0.15,
        terminals=terminals,
        edges=edges,
    )
    pad_a = ExpandedPad(
        component="A", pin="PIN_1", abs_x=2.0, abs_y=5.0, orientation=0.0, kind="fixed"
    )
    pad_b = ExpandedPad(
        component="B", pin="PIN_1", abs_x=2.0, abs_y=9.0, orientation=0.0, kind="fixed"
    )
    artifact = FrontendArtifact(
        project_name="micro",
        board={"width": 20.0, "height": 20.0},
        lint_report=LintReport(),
        components={
            "A": ComponentExpansion(
                name="A",
                footprint_ref=None,
                placement_kind="fixed",
                pads=(pad_a,),
            ),
            "B": ComponentExpansion(
                name="B",
                footprint_ref=None,
                placement_kind="fixed",
                pads=(pad_b,),
            ),
        },
        fixed_terminals={"A.PIN_1": pad_a, "B.PIN_1": pad_b},
    )
    return ir, artifact


def test_locked_edge_length_within_tolerance() -> None:
    ir, artifact = _make_micro_case(target_length=4.0)
    cpsat = build_model(ir, artifact)
    res = solve_model(cpsat, time_limit_s=5.0, num_workers=1)
    assert res.status_name in ("OPTIMAL", "FEASIBLE")
    geom = extract_geometry(ir=ir, artifact=artifact, cpsat=cpsat, result=res)
    audit = audit_geometry(ir, geom)
    assert audit.locked_edges_within_tolerance
    assert audit.max_length_error_fraction <= 0.005 + 1e-9


def test_inconsistent_fixed_pair_is_skipped_not_infeasible() -> None:
    ir, artifact = _make_micro_case(target_length=1.0)
    cpsat = build_model(ir, artifact)
    assert cpsat.skipped_locked_edges
    assert cpsat.skipped_locked_edges[0][0] == "e1"
    res = solve_model(cpsat, time_limit_s=5.0, num_workers=1)
    assert res.status_name in ("OPTIMAL", "FEASIBLE")


def test_audit_no_overlap_pass_for_disjoint_traces() -> None:
    ir, artifact = _make_micro_case(target_length=4.0)
    cpsat = build_model(ir, artifact)
    res = solve_model(cpsat, time_limit_s=5.0, num_workers=1)
    geom = extract_geometry(ir=ir, artifact=artifact, cpsat=cpsat, result=res)
    audit = audit_geometry(
        ir,
        geom,
        skip_length_edges={eid for eid, _ in cpsat.skipped_locked_edges},
        resolved_endpoints=cpsat.edge_endpoint_resolved,
    )
    assert audit.no_overlap_pass
