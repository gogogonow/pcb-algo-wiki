"""M6 svg_full + stub tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from output import export_gds_stub, export_gerber_stub, render_full_layout
from postproc import BendReport, DrcReport, DrcViolation, LvsReport
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass
from schema.v6_ir import Board, Point


def _geom() -> GeometryIR:
    return GeometryIR(
        project="t",
        board=Board(origin=Point(x=0.0, y=0.0), width=20.0, height=10.0),
        routes={
            "e1": RoutePolyline(
                edge_id="e1",
                routing_class=RoutingClass.FLEXIBLE_PATH,
                width=0.254,
                points=(Point(x=0.0, y=5.0), Point(x=20.0, y=5.0)),
            )
        },
        solve_status="OPTIMAL",
        solve_wall_seconds=0.01,
    )


def test_render_full_layout_no_overlay() -> None:
    svg = render_full_layout(_geom())
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_render_full_layout_with_drc_critical_overlay() -> None:
    drc = DrcReport(
        min_trace_width=0.254,
        min_clearance=0.15,
        violations=(
            DrcViolation(
                rule="min_clearance",
                severity="critical",
                edge_a="e1",
                edge_b=None,
                detail="bbox-overlap 999µm < clearance",
            ),
        ),
    )
    svg = render_full_layout(_geom(), drc=drc)
    assert "stroke-dasharray" in svg
    assert "DRC crit=1" in svg


def test_render_full_layout_lvs_skipped_footer() -> None:
    svg = render_full_layout(
        _geom(),
        bend=BendReport(bended_edges=("e1",)),
        lvs=LvsReport(skipped=True, reason="x"),
    )
    assert "LVS=skipped" in svg
    assert "bends=1" in svg


def test_gerber_stub_raises(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="v7"):
        export_gerber_stub(_geom(), tmp_path)


def test_gds_stub_raises(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="v7"):
        export_gds_stub(_geom(), tmp_path / "x.gds")
