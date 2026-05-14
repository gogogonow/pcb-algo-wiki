"""M8 crossing diagnostics tests.

Covers root-cause classification (R1–R5), hotspot detection, and the six
algorithmic GAP detectors plus the top-level ``analyze_crossings`` entry.
"""

from __future__ import annotations

from postproc.crossing_analysis import (
    CRITICAL_OVERLAP_UM,
    R1,
    R2,
    R3,
    R4,
    R5,
    PairFact,
    _AnalysisContext,
    _compute_hotspots,
    _detect_gap_cpsat_no_geom_freedom,
    _detect_gap_data_overspec,
    _detect_gap_fanout_not_dispersed,
    _detect_gap_flex_no_obstacle,
    _detect_gap_placement_density,
    analyze_crossings,
)
from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import RoutingClass, SolverEdge, SolverIR
from schema.v6_ir import Board, Point


def _board(w: float = 100.0, h: float = 100.0) -> Board:
    return Board(origin=Point(x=0.0, y=0.0), width=w, height=h)


def _pt(x: float, y: float) -> Point:
    return Point(x=float(x), y=float(y))


def _route(
    eid: str,
    pts: tuple[Point, ...],
    *,
    width: float = 0.5,
    rc: RoutingClass = RoutingClass.FLEXIBLE_PATH,
) -> RoutePolyline:
    return RoutePolyline(edge_id=eid, routing_class=rc, width=width, points=pts)


def _edge(
    *,
    ep_a: str,
    ep_b: str,
    net: str | None = None,
    rc: RoutingClass = RoutingClass.FLEXIBLE_PATH,
    target_length: float | None = None,
) -> SolverEdge:
    return SolverEdge(
        endpoints=(ep_a, ep_b),
        routing_class=rc,
        net=net,
        target_length=target_length,
    )


def _geom(*routes: RoutePolyline, board: Board | None = None) -> GeometryIR:
    return GeometryIR(
        project="t",
        board=board or _board(),
        routes={r.edge_id: r for r in routes},
        solve_status="OPTIMAL",
        solve_wall_seconds=0.0,
        objective_value=0.0,
    )


def _ir(edges: dict[str, SolverEdge], board: Board | None = None) -> SolverIR:
    return SolverIR(project="t", board=board or _board(), clearance=0.15, edges=edges)


def _pair(edge_a: str, edge_b: str, overlap_um: int = 500):
    class _P:
        pass

    p = _P()
    p.edge_a = edge_a
    p.edge_b = edge_b
    p.overlap_um = overlap_um
    return p


# ──────────────────────────────────────────────────────────────────────────────
# Root-cause classification
# ──────────────────────────────────────────────────────────────────────────────


def test_classify_r1_shared_endpoint() -> None:
    ra = _route("EA", (_pt(0, 0), _pt(10, 0)))
    rb = _route("EB", (_pt(0, 0), _pt(0, 10)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="PIN_X", ep_b="PIN_A", net="N1"),
            "EB": _edge(ep_a="PIN_X", ep_b="PIN_B", net="N2"),
        }
    )
    report = analyze_crossings(g, ir, [_pair("EA", "EB")])
    assert report.pair_facts[0].root_cause == R1
    assert report.pair_facts[0].shared_endpoint == "PIN_X"


def test_classify_r2_same_net_no_shared() -> None:
    ra = _route("EA", (_pt(0, 0), _pt(10, 0)))
    rb = _route("EB", (_pt(5, -2), _pt(5, 5)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="NET_A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="NET_A"),
        }
    )
    report = analyze_crossings(g, ir, [_pair("EA", "EB")])
    assert report.pair_facts[0].root_cause == R2
    assert report.pair_facts[0].same_net is True


def test_classify_r3_cross_net_perpendicular() -> None:
    ra = _route("EA", (_pt(0, 5), _pt(20, 5)))
    rb = _route("EB", (_pt(10, 0), _pt(10, 12)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="NET_A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="NET_B"),
        }
    )
    report = analyze_crossings(g, ir, [_pair("EA", "EB")])
    assert report.pair_facts[0].root_cause == R3


def test_classify_r4_long_over_short_perpendicular() -> None:
    # long horizontal 50mm crosses short vertical 5mm → 10× ratio.
    ra = _route("EA", (_pt(0, 5), _pt(50, 5)))
    rb = _route("EB", (_pt(20, 3), _pt(20, 8)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="NET_A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="NET_B"),
        }
    )
    report = analyze_crossings(g, ir, [_pair("EA", "EB")])
    assert report.pair_facts[0].root_cause == R4


def test_classify_r5_locked_locked_low_angle() -> None:
    ra = _route(
        "EA",
        (_pt(0, 5), _pt(20, 5)),
        rc=RoutingClass.RF_CONSTRAINED_LOCKED,
    )
    rb = _route(
        "EB",
        (_pt(0, 5.2), _pt(20, 5.2)),
        rc=RoutingClass.RF_CONSTRAINED_LOCKED,
    )
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(
                ep_a="P1",
                ep_b="P2",
                net="NA",
                rc=RoutingClass.RF_CONSTRAINED_LOCKED,
                target_length=20.0,
            ),
            "EB": _edge(
                ep_a="P3",
                ep_b="P4",
                net="NB",
                rc=RoutingClass.RF_CONSTRAINED_LOCKED,
                target_length=20.0,
            ),
        }
    )
    report = analyze_crossings(g, ir, [_pair("EA", "EB")])
    assert report.pair_facts[0].root_cause == R5


# ──────────────────────────────────────────────────────────────────────────────
# PairFact metrics
# ──────────────────────────────────────────────────────────────────────────────


def test_pairfact_metrics_populated() -> None:
    ra = _route("EA", (_pt(0, 0), _pt(20, 0)))
    rb = _route("EB", (_pt(10, -5), _pt(10, 5)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="B"),
        }
    )
    rep = analyze_crossings(g, ir, [_pair("EA", "EB", overlap_um=2500)])
    f = rep.pair_facts[0]
    assert f.overlap_um == 2500
    assert 89.0 <= f.intersection_angle_deg <= 90.0
    assert f.edge_a_length_mm == 20.0
    assert abs(f.edge_b_length_mm - 10.0) < 1e-6
    assert f.nearest_distance_um == 0  # they cross
    assert rep.critical_pair_count == 1


def test_critical_threshold() -> None:
    ra = _route("EA", (_pt(0, 0), _pt(20, 0)))
    rb = _route("EB", (_pt(10, -5), _pt(10, 5)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="B"),
        }
    )
    rep = analyze_crossings(
        g, ir, [_pair("EA", "EB", overlap_um=CRITICAL_OVERLAP_UM - 1)]
    )
    assert rep.critical_pair_count == 0


# ──────────────────────────────────────────────────────────────────────────────
# Hotspot detection
# ──────────────────────────────────────────────────────────────────────────────


def test_hotspots_detected_when_clustered() -> None:
    g = _geom(board=_board(100.0, 100.0))
    # 10 pairs all in cell (0,0) (centre 0–20mm)
    facts = [
        PairFact(
            edge_a=f"E{i}A",
            edge_b=f"E{i}B",
            overlap_um=100,
            overlap_area_um2=0,
            nearest_distance_um=0,
            intersection_angle_deg=90.0,
            edge_a_class="x",
            edge_b_class="x",
            edge_a_length_mm=1.0,
            edge_b_length_mm=1.0,
            shared_endpoint=None,
            same_net=False,
            root_cause=R3,
            centre_x_mm=5.0,
            centre_y_mm=5.0,
        )
        for i in range(10)
    ]
    hotspots = _compute_hotspots(facts, g)
    assert len(hotspots) >= 1
    assert hotspots[0].contained_pair_count == 10


def test_hotspots_empty_when_dispersed() -> None:
    g = _geom(board=_board(100.0, 100.0))
    facts = []
    for i in range(10):
        # spread across all 25 cells
        cx = (i % 5) * 20 + 5
        cy = (i // 5) * 20 + 5
        facts.append(
            PairFact(
                edge_a=f"E{i}A",
                edge_b=f"E{i}B",
                overlap_um=100,
                overlap_area_um2=0,
                nearest_distance_um=0,
                intersection_angle_deg=90.0,
                edge_a_class="x",
                edge_b_class="x",
                edge_a_length_mm=1.0,
                edge_b_length_mm=1.0,
                shared_endpoint=None,
                same_net=False,
                root_cause=R3,
                centre_x_mm=cx,
                centre_y_mm=cy,
            )
        )
    hotspots = _compute_hotspots(facts, g)
    assert hotspots == []


# ──────────────────────────────────────────────────────────────────────────────
# GAP detectors
# ──────────────────────────────────────────────────────────────────────────────


def _ctx(facts: list[PairFact], **kwargs) -> _AnalysisContext:
    g = _geom(board=_board(100.0, 100.0))
    ir = _ir({})
    ctx = _AnalysisContext(geom=g, solver_ir=ir, pair_facts=facts)
    for f in facts:
        ctx.root_cause_counts[f.root_cause] = (
            ctx.root_cause_counts.get(f.root_cause, 0) + 1
        )
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


def _mk_fact(rc: str = R3, **overrides) -> PairFact:
    base = dict(
        edge_a="EA",
        edge_b="EB",
        overlap_um=100,
        overlap_area_um2=0,
        nearest_distance_um=0,
        intersection_angle_deg=90.0,
        edge_a_class="rf_constrained_locked",
        edge_b_class="rf_constrained_locked",
        edge_a_length_mm=10.0,
        edge_b_length_mm=10.0,
        shared_endpoint=None,
        same_net=False,
        root_cause=rc,
        centre_x_mm=5.0,
        centre_y_mm=5.0,
    )
    base.update(overrides)
    return PairFact(**base)


def test_gap_cpsat_triggered_when_r5_majority() -> None:
    facts = [_mk_fact(R5) for _ in range(10)] + [_mk_fact(R3)]
    gap = _detect_gap_cpsat_no_geom_freedom(_ctx(facts))
    assert gap is not None
    assert gap.gap_id == "GAP-CPSAT-NO-GEOM-FREEDOM"
    assert gap.affected_pair_count == 10


def test_gap_cpsat_silent_when_no_r5() -> None:
    facts = [_mk_fact(R3) for _ in range(10)]
    assert _detect_gap_cpsat_no_geom_freedom(_ctx(facts)) is None


def test_gap_fanout_triggered() -> None:
    facts = [_mk_fact(R1, shared_endpoint="PIN_X") for _ in range(5)] + [
        _mk_fact(R3) for _ in range(5)
    ]
    gap = _detect_gap_fanout_not_dispersed(_ctx(facts))
    assert gap is not None
    assert "PIN_X" in gap.evidence


def test_gap_flex_no_obstacle_triggered() -> None:
    facts = [_mk_fact(R3, edge_a_class="flexible_path") for _ in range(3)]
    gap = _detect_gap_flex_no_obstacle(_ctx(facts))
    assert gap is not None
    assert gap.affected_pair_count == 3


def test_gap_meander_collision_triggered() -> None:
    # Need facts whose route in geom has >=6 points.
    pts_meander = tuple(_pt(i, 0) for i in range(7))
    pts_simple = (_pt(0, 1), _pt(20, 1))
    ra = _route("EA", pts_meander)
    rb = _route("EB", pts_simple)
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="B"),
        }
    )
    rep = analyze_crossings(g, ir, [_pair("EA", "EB")])
    ids = [g.gap_id for g in rep.gaps]
    assert "GAP-MEANDER-COLLISION" in ids


def test_gap_data_overspec_triggered_with_semantic_count() -> None:
    facts = [_mk_fact(R5) for _ in range(3)]
    gap = _detect_gap_data_overspec(_ctx(facts, semantic_issue_count=4))
    assert gap is not None
    assert gap.affected_pair_count == 4


def test_gap_data_overspec_silent_when_zero() -> None:
    facts = [_mk_fact(R5) for _ in range(3)]
    assert _detect_gap_data_overspec(_ctx(facts, semantic_issue_count=0)) is None


def test_gap_placement_density_triggered() -> None:
    # 10 facts all clustered in one cell.
    facts = [_mk_fact(R3, centre_x_mm=5.0, centre_y_mm=5.0) for _ in range(10)]
    g = _geom(board=_board(100.0, 100.0))
    ir = _ir({})
    ctx = _AnalysisContext(geom=g, solver_ir=ir, pair_facts=facts)
    for f in facts:
        ctx.root_cause_counts[f.root_cause] = (
            ctx.root_cause_counts.get(f.root_cause, 0) + 1
        )
    ctx.hotspots = _compute_hotspots(facts, g)
    gap = _detect_gap_placement_density(ctx)
    assert gap is not None
    assert gap.affected_pair_count == 10


# ──────────────────────────────────────────────────────────────────────────────
# Empty / robustness
# ──────────────────────────────────────────────────────────────────────────────


def test_analyze_crossings_empty_input() -> None:
    g = _geom()
    ir = _ir({})
    rep = analyze_crossings(g, ir, [])
    assert rep.total_pairs == 0
    assert rep.gaps == ()
    assert rep.region_hotspots == ()


def test_analyze_crossings_skips_unknown_edges() -> None:
    g = _geom()
    ir = _ir({})
    rep = analyze_crossings(g, ir, [_pair("MISSING_A", "MISSING_B")])
    assert rep.total_pairs == 0


def test_analyze_crossings_priority_sort() -> None:
    """GAP list is sorted by priority_score descending."""
    facts = [_mk_fact(R5) for _ in range(20)] + [
        _mk_fact(R3, edge_a_class="flexible_path") for _ in range(2)
    ]
    g = _geom(board=_board(100.0, 100.0))
    ir = _ir({})
    ctx = _AnalysisContext(geom=g, solver_ir=ir, pair_facts=facts)
    for f in facts:
        ctx.root_cause_counts[f.root_cause] = (
            ctx.root_cause_counts.get(f.root_cause, 0) + 1
        )
    g1 = _detect_gap_cpsat_no_geom_freedom(ctx)
    g2 = _detect_gap_flex_no_obstacle(ctx)
    assert g1 is not None and g2 is not None
    # CPSAT (L weight=8) has 20 eliminated → 2.5 score
    # Flex (S weight=1) has 2 eliminated → 2.0 score
    assert g1.priority_score > g2.priority_score


def test_dict_overlap_pair_accepted() -> None:
    ra = _route("EA", (_pt(0, 0), _pt(20, 0)))
    rb = _route("EB", (_pt(10, -5), _pt(10, 5)))
    g = _geom(ra, rb)
    ir = _ir(
        {
            "EA": _edge(ep_a="P1", ep_b="P2", net="A"),
            "EB": _edge(ep_a="P3", ep_b="P4", net="B"),
        }
    )
    rep = analyze_crossings(
        g, ir, [{"edge_a": "EA", "edge_b": "EB", "overlap_um": 500}]
    )
    assert rep.total_pairs == 1
