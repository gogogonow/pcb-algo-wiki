"""Task 3 — floating airwire pairwise crossing cost."""

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
    _airwire_cross_cost,
)
from solver.v2.skeleton_router import SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport


def _two_floating_artifact() -> FrontendArtifact:
    """Two fixed pins TP_A (0,0) / TP_B (10,0) plus two floating components
    R1, R2 each connected to one of them.  We test crossing of airwires
    R1→TP_A and R2→TP_B under two state configurations."""
    pads_fixed = {
        "TP_A": ExpandedPad(
            component="TP_A", pin="P", abs_x=0.0, abs_y=0.0, orientation=0.0, kind="testpoint"
        ),
        "TP_B": ExpandedPad(
            component="TP_B", pin="P", abs_x=10.0, abs_y=0.0, orientation=0.0, kind="testpoint"
        ),
    }
    fixed_components = {
        n: ComponentExpansion(name=n, footprint_ref=None, placement_kind="fixed", pads=(p,))
        for n, p in pads_fixed.items()
    }
    r_pad = lambda name: ExpandedPad(
        component=name, pin="PIN_1", abs_x=None, abs_y=None, orientation=None, kind="smd",
        local_x=0.0, local_y=0.0,
    )
    floating = {
        n: ComponentExpansion(
            name=n, footprint_ref=None, placement_kind="floating",
            pads=(r_pad(n),), bbox=None, footprint_dims_mm=(1.0, 1.0),
        )
        for n in ("R1", "R2")
    }
    edges = {
        "flex_r1_a": TriagedEdge(
            name="flex_r1_a", edge_type="signal", routing_class="flexible_path",
            target_length=None, width=0.2, connections=("R1.PIN_1", "TP_A.P"),
        ),
        "flex_r2_b": TriagedEdge(
            name="flex_r2_b", edge_type="signal", routing_class="flexible_path",
            target_length=None, width=0.2, connections=("R2.PIN_1", "TP_B.P"),
        ),
    }
    return FrontendArtifact(
        project_name="test",
        board={"width_mm": 10.0, "height_mm": 10.0},
        lint_report=LintReport(),
        components={**fixed_components, **floating},
        edges=edges,
    )


def test_parallel_vs_crossed_airwires() -> None:
    artifact = _two_floating_artifact()
    skel = SkeletonReport()
    adh = UvAdhesionReport()
    # Parallel: R1 above TP_A, R2 above TP_B — no crossing.
    parallel = {
        "R1": FloatingPlacement(x=0.0, y=5.0, rotation=0),
        "R2": FloatingPlacement(x=10.0, y=5.0, rotation=0),
    }
    # Crossed (X-shape): R1 above TP_B, R2 above TP_A.
    crossed = {
        "R1": FloatingPlacement(x=10.0, y=5.0, rotation=0),
        "R2": FloatingPlacement(x=0.0, y=5.0, rotation=0),
    }
    cost_parallel = _airwire_cross_cost(artifact, parallel, skel, adh)
    cost_crossed = _airwire_cross_cost(artifact, crossed, skel, adh)
    assert cost_parallel == 0.0
    assert cost_crossed >= 1.0


def test_airwire_cross_weight_positive() -> None:
    assert FloatingPlacerConfig().airwire_cross_weight > 0
