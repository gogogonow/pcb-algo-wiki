"""Unit tests for _make_viewer_components and _collect_skeleton_routes."""

from __future__ import annotations

import pytest

from tools.pcb_solve_v2 import _make_viewer_components, _collect_skeleton_routes
from frontend.models import (
    FrontendArtifact,
    ComponentExpansion,
    BBox,
    ExpandedPad,
    LintReport,
    TriagedEdge,
)
from solver.v2.skeleton_router import RouteOutcome, SkeletonReport


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
    (result2,) = _collect_skeleton_routes(skeleton, _artifact({}, edges={"e1": _edge(None)}))
    assert result2["width_mm"] == 0.0


def test_collect_skeleton_routes_failed_route() -> None:
    skeleton = _skeleton({"e1": _route(success=False, failure_reason="no_path")})
    artifact = _artifact({})
    (result,) = _collect_skeleton_routes(skeleton, artifact)
    assert result["success"] is False
    assert result["failure_reason"] == "no_path"
