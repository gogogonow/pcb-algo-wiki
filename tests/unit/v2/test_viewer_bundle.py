"""Unit tests for _make_viewer_components and _collect_skeleton_routes."""

from __future__ import annotations

import json

import pytest

from tools.pcb_solve_v2 import (
    _collect_skeleton_routes,
    _emit_viewer_bundle,
    _make_viewer_components,
)
from frontend.models import (
    ComponentExpansion,
    BBox,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
    NormalizedNode,
    TriagedEdge,
    UvMeta,
)
from schema.geometry_ir import GeometryIR
from schema.v6_ir import Board, Point
from solver.v2.node_planner import NodePlan
from solver.v2.orchestrator import (
    OrchestratorV2Result,
    PhaseAResult,
    PhaseBResult,
    PhaseCResult,
)
from solver.v2.skeleton_router import RouteOutcome, SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _artifact(components: dict, edges: dict | None = None) -> FrontendArtifact:
    return FrontendArtifact(
        project_name=None,
        board={},
        lint_report=LintReport(),
        components=components,
        edges=edges or {},
    )


def _comp(
    *,
    bbox: BBox | None = None,
    placement_kind: str = "fixed",
    pads: tuple[ExpandedPad, ...] = (),
) -> ComponentExpansion:
    return ComponentExpansion(
        name="test",
        footprint_ref=None,
        placement_kind=placement_kind,  # type: ignore[arg-type]
        pads=pads,
        bbox=bbox,
    )


def _pad(pin: str, abs_x: float | None, abs_y: float | None) -> ExpandedPad:
    return ExpandedPad(
        component="test",
        pin=pin,
        abs_x=abs_x,
        abs_y=abs_y,
        orientation=None,
        kind="fixed",
    )


def _route(
    *,
    edge_id: str = "e1",
    polyline_um: tuple[tuple[int, int], ...] = (),
    length_mm: float = 0.0,
    target_mm: float | None = None,
    success: bool = True,
    rip_up_round: int = 0,
    failure_reason: str | None = None,
) -> RouteOutcome:
    return RouteOutcome(
        edge_id=edge_id,
        polyline_um=polyline_um,
        length_mm=length_mm,
        target_mm=target_mm,
        success=success,
        rip_up_round=rip_up_round,
        failure_reason=failure_reason,
    )


def _skeleton(routes: dict[str, RouteOutcome]) -> SkeletonReport:
    return SkeletonReport(routes=routes)


def _edge(width: float | None) -> TriagedEdge:
    return TriagedEdge(
        name="e",
        edge_type="signal",
        routing_class="signal",
        target_length=None,
        width=width,
        connections=(),
    )


# ---------------------------------------------------------------------------
# _make_viewer_components
# ---------------------------------------------------------------------------


def test_make_viewer_components_with_bbox() -> None:
    artifact = _artifact({"U1": _comp(bbox=BBox(0.0, 0.0, 4.0, 2.0))})
    (result,) = _make_viewer_components(artifact)
    assert result["x_mm"] == 2.0
    assert result["y_mm"] == 1.0
    assert result["w_mm"] == 4.0
    assert result["h_mm"] == 2.0
    assert result["rotation_deg"] == 0.0


def test_make_viewer_components_bbox_fallback() -> None:
    artifact = _artifact({"U1": _comp(bbox=None)})
    (result,) = _make_viewer_components(artifact)
    assert result["x_mm"] == 0.0
    assert result["y_mm"] == 0.0
    assert result["w_mm"] == 1.0
    assert result["h_mm"] == 1.0


def test_make_viewer_components_kind_uv() -> None:
    artifact = _artifact({"U1": _comp(placement_kind="parametric_uv")})
    (result,) = _make_viewer_components(artifact)
    assert result["kind"] == "uv"


def test_make_viewer_components_kind_passthrough() -> None:
    artifact = _artifact(
        {
            "U1": _comp(placement_kind="fixed"),
            "U2": _comp(placement_kind="floating"),
        }
    )
    by_ref = {r["ref"]: r for r in _make_viewer_components(artifact)}
    assert by_ref["U1"]["kind"] == "fixed"
    assert by_ref["U2"]["kind"] == "floating"


def test_make_viewer_components_pads_included() -> None:
    pads = (_pad("1", 1.5, 2.5), _pad("2", 3.0, 4.0))
    artifact = _artifact({"U1": _comp(pads=pads)})
    (result,) = _make_viewer_components(artifact)
    assert len(result["pads"]) == 2
    assert result["pads"][0] == {"pin": "1", "x_mm": 1.5, "y_mm": 2.5}


def test_make_viewer_components_pads_filtered() -> None:
    pads = (_pad("1", None, 2.5), _pad("2", 3.0, None), _pad("3", 1.0, 1.0))
    artifact = _artifact({"U1": _comp(pads=pads)})
    (result,) = _make_viewer_components(artifact)
    assert len(result["pads"]) == 1
    assert result["pads"][0]["pin"] == "3"


def test_make_viewer_components_ref_preserved() -> None:
    artifact = _artifact({"R42": _comp()})
    (result,) = _make_viewer_components(artifact)
    assert result["ref"] == "R42"


# ---------------------------------------------------------------------------
# _collect_skeleton_routes
# ---------------------------------------------------------------------------


def test_collect_skeleton_routes_um_to_mm() -> None:
    skeleton = _skeleton({"e1": _route(polyline_um=((1000, 2000), (3000, 4000)))})
    artifact = _artifact({})
    (result,) = _collect_skeleton_routes(skeleton, artifact)
    assert result["polyline_mm"] == [[1.0, 2.0], [3.0, 4.0]]


def test_collect_skeleton_routes_width_from_edge() -> None:
    skeleton = _skeleton({"e1": _route()})
    artifact = _artifact({}, edges={"e1": _edge(0.1)})
    (result,) = _collect_skeleton_routes(skeleton, artifact)
    assert result["width_mm"] == pytest.approx(0.1)


def test_collect_skeleton_routes_missing_edge_width() -> None:
    skeleton = _skeleton({"e1": _route()})

    # edge not present at all
    (result,) = _collect_skeleton_routes(skeleton, _artifact({}))
    assert result["width_mm"] == 0.0

    # edge present but width=None
    (result2,) = _collect_skeleton_routes(
        skeleton, _artifact({}, edges={"e1": _edge(None)})
    )
    assert result2["width_mm"] == 0.0


def test_collect_skeleton_routes_failed_route() -> None:
    skeleton = _skeleton({"e1": _route(success=False, failure_reason="no_path")})
    artifact = _artifact({})
    (result,) = _collect_skeleton_routes(skeleton, artifact)
    assert result["success"] is False
    assert result["failure_reason"] == "no_path"


def _minimal_orchestrator_result_for_prea() -> OrchestratorV2Result:
    fixed_comp = ComponentExpansion(
        name="U1",
        footprint_ref="FP_U1",
        placement_kind="fixed",
        pads=(
            ExpandedPad(
                component="U1",
                pin="PIN_2",
                abs_x=10.0,
                abs_y=10.0,
                orientation=0.0,
                kind="fixed",
            ),
        ),
        bbox=BBox(9.5, 9.0, 10.5, 11.0),
    )
    r1_uv = ComponentExpansion(
        name="R1",
        footprint_ref="FP_R",
        placement_kind="parametric_uv",
        pads=(
            ExpandedPad(
                component="R1",
                pin="PIN_1",
                abs_x=None,
                abs_y=None,
                orientation=None,
                kind="uv_deferred",
                local_x=-0.5,
                local_y=0.0,
            ),
            ExpandedPad(
                component="R1",
                pin="PIN_2",
                abs_x=None,
                abs_y=None,
                orientation=None,
                kind="uv_deferred",
                local_x=0.5,
                local_y=0.0,
            ),
        ),
        uv_meta=UvMeta(anchor_pin="PIN_1", reference_net="$N1"),
    )
    c1_uv = ComponentExpansion(
        name="C1",
        footprint_ref="FP_C",
        placement_kind="parametric_uv",
        pads=(
            ExpandedPad(
                component="C1",
                pin="PIN_1",
                abs_x=None,
                abs_y=None,
                orientation=None,
                kind="uv_deferred",
                local_x=-0.5,
                local_y=0.0,
            ),
            ExpandedPad(
                component="C1",
                pin="PIN_2",
                abs_x=None,
                abs_y=None,
                orientation=None,
                kind="uv_deferred",
                local_x=0.5,
                local_y=0.0,
            ),
        ),
        uv_meta=UvMeta(anchor_pin="PIN_1", reference_net="$N1"),
    )
    edges = {
        "seg1": TriagedEdge(
            name="seg1",
            edge_type="microstrip",
            routing_class="rf_constrained_locked",
            target_length=5.0,
            width=1.0,
            connections=("U1.PIN_2", "N1"),
            net="$N1",
        ),
        "seg2": TriagedEdge(
            name="seg2",
            edge_type="microstrip",
            routing_class="rf_constrained_locked",
            target_length=1.3,
            width=1.8,
            connections=("N1", "R1.PIN_1"),
            net="$N1",
        ),
        "seg3": TriagedEdge(
            name="seg3",
            edge_type="microstrip",
            routing_class="rf_constrained_locked",
            target_length=1.5,
            width=1.2,
            connections=("N1", "C1.PIN_1"),
            net="$N1",
        ),
    }
    artifact = FrontendArtifact(
        project_name="P",
        board={"width": 40.0, "height": 100.0},
        lint_report=LintReport(),
        components={"U1": fixed_comp, "R1": r1_uv, "C1": c1_uv},
        fixed_terminals={"U1.PIN_2": fixed_comp.pads[0]},
        uv_components={"R1": r1_uv, "C1": c1_uv},
        edges=edges,
        nodes={
            "N1": NormalizedNode(
                name="N1",
                original_type="universal_junction",
                normalized_type="universal_junction",
            )
        },
    )
    plan = NodePlan(
        endpoint_xy={
            "U1.PIN_2": (10.0, 10.0),
            "N1": (15.0, 10.0),
            "R1.PIN_1": (16.3, 10.0),
            "C1.PIN_1": (15.0, 8.5),
            "R1.PIN_2": (17.2, 10.0),
            "C1.PIN_2": (15.0, 7.5),
        }
    )
    skeleton = SkeletonReport(
        routes={},
        final_endpoint_um={
            "U1.PIN_2": (10000, 10000),
            "N1": (15000, 10000),
        },
    )
    geometry = GeometryIR(
        project="P",
        board=Board(
            origin=Point(x=0.0, y=0.0),
            width=40.0,
            height=100.0,
        ),
        solve_status="UNKNOWN",
        solve_wall_seconds=0.0,
    )
    return OrchestratorV2Result(
        artifact=artifact,
        phase_a=PhaseAResult(skeleton=skeleton, plan=plan, wall_seconds=0.0),
        phase_b=PhaseBResult(adhesion=UvAdhesionReport(), wall_seconds=0.0),
        phase_c=PhaseCResult(
            routed_flex_edges=[], failed_flex_edges=[], wall_seconds=0.0
        ),
        geometry=geometry,
    )


def test_emit_viewer_bundle_prea_uses_prea_positions_not_skeleton_only(
    tmp_path,
) -> None:
    result = _minimal_orchestrator_result_for_prea()
    out = _emit_viewer_bundle(result, tmp_path)
    data = json.loads(out.read_text())
    prea_edges = {e["edge_id"]: e for e in data["phases"]["preA"]["edges"]}
    # seg2/seg3 should still be drawable even though skeleton.final_endpoint_um
    # only carries seg1 endpoints in this fixture.
    assert set(prea_edges["seg2"]["endpoint_positions_mm"]) == {"N1", "R1.PIN_1"}
    assert set(prea_edges["seg3"]["endpoint_positions_mm"]) == {"N1", "C1.PIN_1"}


def test_emit_viewer_bundle_prea_includes_uv_placements(tmp_path) -> None:
    result = _minimal_orchestrator_result_for_prea()
    out = _emit_viewer_bundle(result, tmp_path)
    data = json.loads(out.read_text())
    placements = data["phases"]["preA"]["uv_placements"]
    refs = {p["ref"] for p in placements}
    assert refs == {"R1", "C1"}


def test_emit_viewer_bundle_prea_edges_have_scene_field(tmp_path) -> None:
    """Each preA edge in the bundle must carry a 'scene' field."""
    result = _minimal_orchestrator_result_for_prea()
    out = _emit_viewer_bundle(result, tmp_path)
    data = json.loads(out.read_text())
    valid_scenes = {
        "scene_1_fixed_tree",
        "scene_2_shunt_uv",
        "scene_3_floating",
        "not_applicable",
    }
    for edge in data["phases"]["preA"]["edges"]:
        assert "scene" in edge, f"Edge {edge['edge_id']} missing 'scene' field"
        assert edge["scene"] in valid_scenes, (
            f"Edge {edge['edge_id']} has unknown scene: {edge['scene']!r}"
        )
