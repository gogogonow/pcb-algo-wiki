"""Task 2 — RF bus crossing cost in floating placer."""

from frontend.models import (
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
    TriagedEdge,
)
from solver.v2.floating_placer import (
    FloatingPlacement,
    FloatingPlacerConfig,
    _rf_bus_cross_cost,
)
from solver.v2.skeleton_router import RouteOutcome, SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport


def _mini_artifact() -> FrontendArtifact:
    """Single fixed pin TP at (5, 0) + one floating component R with two
    pads (PIN_1 / PIN_2)."""
    tp_pad = ExpandedPad(
        component="TP", pin="P", abs_x=5.0, abs_y=0.0, orientation=0.0, kind="testpoint"
    )
    tp = ComponentExpansion(
        name="TP",
        footprint_ref=None,
        placement_kind="fixed",
        pads=(tp_pad,),
        bbox=None,
    )
    r_pad1 = ExpandedPad(
        component="R",
        pin="PIN_1",
        abs_x=None,
        abs_y=None,
        orientation=None,
        kind="smd",
        local_x=-0.5,
        local_y=0.0,
    )
    r_pad2 = ExpandedPad(
        component="R",
        pin="PIN_2",
        abs_x=None,
        abs_y=None,
        orientation=None,
        kind="smd",
        local_x=0.5,
        local_y=0.0,
    )
    r = ComponentExpansion(
        name="R",
        footprint_ref=None,
        placement_kind="floating",
        pads=(r_pad1, r_pad2),
        bbox=None,
        footprint_dims_mm=(1.0, 1.0),
    )
    edge = TriagedEdge(
        name="flex_r_tp",
        edge_type="signal",
        routing_class="flexible_path",
        target_length=None,
        width=0.2,
        connections=("R.PIN_1", "TP.P"),
    )
    return FrontendArtifact(
        project_name="test",
        board={"width_mm": 10.0, "height_mm": 10.0},
        lint_report=LintReport(),
        components={"TP": tp, "R": r},
        edges={"flex_r_tp": edge},
    )


def _bus_skeleton() -> SkeletonReport:
    """Locked RF route from (0, 2) to (10, 2) — a horizontal bus."""
    skel = SkeletonReport()
    skel.routes["PWR_BUS"] = RouteOutcome(
        edge_id="PWR_BUS",
        polyline_um=((0, 2000), (10000, 2000)),
        length_mm=10.0,
        target_mm=None,
        success=True,
        rip_up_round=0,
    )
    return skel


def test_rf_bus_cross_cost_distinguishes_sides() -> None:
    artifact = _mini_artifact()
    skel = _bus_skeleton()
    adh = UvAdhesionReport()
    # State A: floating R below the bus (same side as TP) — airwire does NOT cross.
    # R at (5,1) → PIN_1 abs (4.5,1).  TP at (5,0).  Segment (4.5,1)→(5,0) is
    # entirely below y=2 — no intersection.
    state_a = {"R": FloatingPlacement(x=5.0, y=1.0, rotation=0)}
    cost_a = _rf_bus_cross_cost(artifact, state_a, skel, adh)
    # State B: floating R above the bus — airwire (4.5,5)→(5,0) CROSSES y=2 line.
    state_b = {"R": FloatingPlacement(x=5.0, y=5.0, rotation=0)}
    cost_b = _rf_bus_cross_cost(artifact, state_b, skel, adh)
    assert cost_a == 0.0
    assert cost_b >= 1.0


def test_rf_bus_cross_weight_dominates_hpwl() -> None:
    cfg = FloatingPlacerConfig()
    # Worst-case airwire length difference (close vs far) ~= 5mm × hpwl_weight 20 = 100.
    # rf_bus_cross_weight × 1 crossing = 50.  But with weight=50 + airwire_cross_weight,
    # the differential cost of crossing exceeds the hpwl saving of being closer.
    # Specifically: a single crossing costs 50; saving 1mm of HPWL costs 20 — so a
    # placer that crosses to save up to 2mm of length will still be rejected.
    assert cfg.rf_bus_cross_weight >= 40.0
    assert cfg.rf_bus_cross_weight > cfg.hpwl_weight * 2.0
