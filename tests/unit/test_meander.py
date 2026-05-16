"""M7 meander routing unit tests."""

from __future__ import annotations


from postproc.meander import (
    _polyline_length,
    _seg_length,
    apply_meanders,
)
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _board() -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=200.0, height=200.0)


def _route(eid: str, width: float, pts: tuple[Point, ...]) -> RoutePolyline:
    return RoutePolyline(
        edge_id=eid,
        routing_class=RoutingClass.FLEXIBLE_PATH,
        width=width,
        points=pts,
    )


def _edge(target_mm: float | None = None, net: str = "RF") -> SolverEdge:
    return SolverEdge(
        endpoints=("A", "B"),
        routing_class=RoutingClass.FLEXIBLE_PATH,
        net=net,
        target_length=target_mm,
    )


def _geom(*routes: RoutePolyline) -> GeometryIR:
    return GeometryIR(
        project="test",
        board=_board(),
        routes={r.edge_id: r for r in routes},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
        objective_value=0.0,
    )


def _ir(edges: dict[str, SolverEdge]) -> SolverIR:
    return SolverIR(project="test", board=_board(), clearance=0.15, edges=edges)


# ──────────────────────────────────────────────────────────────────────────────
# Helper geometry tests
# ──────────────────────────────────────────────────────────────────────────────


def test_seg_length_simple() -> None:
    p1 = Point(x=0.0, y=0.0)
    p2 = Point(x=3.0, y=4.0)
    assert abs(_seg_length(p1, p2) - 5.0) < 1e-9


def test_polyline_length_two_segments() -> None:
    pts = (
        Point(x=0.0, y=0.0),
        Point(x=3.0, y=0.0),
        Point(x=3.0, y=4.0),
    )
    assert abs(_polyline_length(pts) - 7.0) < 1e-9


# ──────────────────────────────────────────────────────────────────────────────
# apply_meanders: skip cases
# ──────────────────────────────────────────────────────────────────────────────


def test_no_meander_when_no_target() -> None:
    """Edge without target_length → not touched."""
    r = _route("E1", 0.5, (Point(x=0.0, y=0.0), Point(x=50.0, y=0.0)))
    g = _geom(r)
    ir = _ir({"E1": _edge(target_mm=None)})
    new_g, report = apply_meanders(g, ir)
    assert new_g.routes["E1"].points == r.points
    assert report.total_meandered == 0


def test_no_meander_when_already_long_enough() -> None:
    """Route already meets or exceeds target → not touched."""
    r = _route("E1", 0.5, (Point(x=0.0, y=0.0), Point(x=100.0, y=0.0)))
    g = _geom(r)
    ir = _ir({"E1": _edge(target_mm=90.0)})  # actual=100, target=90 → no meander
    new_g, report = apply_meanders(g, ir)
    assert new_g.routes["E1"].points == r.points
    assert report.total_meandered == 0


def test_meander_edge_not_in_geom_emits_warning() -> None:
    """Edge with target but missing from GeometryIR routes → warning."""
    g = _geom()  # empty routes
    ir = _ir({"E1": _edge(target_mm=50.0)})
    _, report = apply_meanders(g, ir)
    assert len(report.warnings) >= 1
    assert "E1" in report.warnings[0]


# ──────────────────────────────────────────────────────────────────────────────
# apply_meanders: success cases
# ──────────────────────────────────────────────────────────────────────────────


def test_meander_applied_and_length_increases() -> None:
    """Long straight trace: meander should increase length toward target."""
    actual_pts = (Point(x=0.0, y=0.0), Point(x=100.0, y=0.0))
    r = _route("E1", 1.0, actual_pts)
    g = _geom(r)
    target = 120.0  # need 20mm extra
    ir = _ir({"E1": _edge(target_mm=target)})
    new_g, report = apply_meanders(g, ir)

    new_pts = new_g.routes["E1"].points
    new_len = _polyline_length(new_pts)

    assert report.total_meandered == 1
    assert report.meandered_edges[0].n_loops > 0
    # New length should be significantly longer than original 100mm.
    assert new_len > 105.0
    # Should not overshoot by more than 5mm.
    assert new_len <= target + 5.0


def test_meander_report_has_correct_fields() -> None:
    """MeanderEdgeResult fields are populated correctly."""
    actual_pts = (Point(x=0.0, y=0.0), Point(x=80.0, y=0.0))
    r = _route("RF_IN", 2.0, actual_pts)
    g = _geom(r)
    ir = _ir({"RF_IN": _edge(target_mm=100.0)})
    _, report = apply_meanders(g, ir)

    assert report.total_meandered == 1
    res = report.meandered_edges[0]
    assert res.edge_id == "RF_IN"
    assert abs(res.original_length_mm - 80.0) < 0.01
    assert res.target_length_mm == 100.0
    assert res.n_loops >= 1
    assert res.loop_height_mm > 0.0
    assert not res.skipped


def test_meander_multiple_edges() -> None:
    """Two edges both needing meanders → both processed independently."""
    r1 = _route("E1", 1.0, (Point(x=0.0, y=20.0), Point(x=50.0, y=20.0)))
    r2 = _route("E2", 0.5, (Point(x=0.0, y=80.0), Point(x=50.0, y=80.0)))
    g = _geom(r1, r2)
    ir = _ir(
        {
            "E1": _edge(target_mm=65.0),
            "E2": _edge(target_mm=60.0),
        }
    )
    new_g, report = apply_meanders(g, ir)
    assert report.total_meandered == 2
    for eid in ("E1", "E2"):
        new_len = _polyline_length(new_g.routes[eid].points)
        assert new_len > 50.0


def test_meander_geom_immutable_original_unchanged() -> None:
    """Original GeometryIR must not be mutated."""
    pts = (Point(x=0.0, y=0.0), Point(x=70.0, y=0.0))
    r = _route("E1", 1.5, pts)
    g = _geom(r)
    ir = _ir({"E1": _edge(target_mm=90.0)})
    new_g, _ = apply_meanders(g, ir)
    # Original geom untouched
    assert g.routes["E1"].points == pts
    # New geom has different points
    assert new_g.routes["E1"].points != pts


def test_meander_preserves_edge_id_and_width() -> None:
    """After meander, edge_id and width of the route must be unchanged."""
    pts = (Point(x=0.0, y=0.0), Point(x=60.0, y=0.0))
    r = _route("SIG_A", 3.6, pts)
    g = _geom(r)
    ir = _ir({"SIG_A": _edge(target_mm=80.0)})
    new_g, _ = apply_meanders(g, ir)
    assert new_g.routes["SIG_A"].edge_id == "SIG_A"
    assert new_g.routes["SIG_A"].width == 3.6
