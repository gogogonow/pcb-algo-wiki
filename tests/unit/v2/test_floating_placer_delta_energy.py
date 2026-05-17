from __future__ import annotations

import pytest

from frontend.models import (
    BBox,
    ComponentExpansion,
    ExpandedPad,
    FrontendArtifact,
    LintReport,
)
from solver.v2.floating_placer import (
    FloatingPlacement,
    FloatingPlacerConfig,
    _build_connected_pairs,
    _static_delta_for_move,
    _static_energy_full,
)


def _artifact() -> FrontendArtifact:
    fixed = ComponentExpansion(
        name="U1",
        footprint_ref=None,
        placement_kind="fixed",
        pads=(
            ExpandedPad(
                component="U1",
                pin="P1",
                abs_x=5.0,
                abs_y=5.0,
                orientation=0.0,
                kind="smd",
            ),
        ),
        bbox=BBox(min_x=4.0, min_y=4.0, max_x=6.0, max_y=6.0),
    )
    f1 = ComponentExpansion(
        name="C1",
        footprint_ref=None,
        placement_kind="floating",
        pads=(
            ExpandedPad(
                component="C1",
                pin="PIN_1",
                abs_x=None,
                abs_y=None,
                orientation=None,
                local_x=-0.5,
                local_y=0.0,
                kind="smd",
            ),
            ExpandedPad(
                component="C1",
                pin="PIN_2",
                abs_x=None,
                abs_y=None,
                orientation=None,
                local_x=0.5,
                local_y=0.0,
                kind="smd",
            ),
        ),
        footprint_dims_mm=(1.0, 1.8),
    )
    f2 = ComponentExpansion(
        name="R1",
        footprint_ref=None,
        placement_kind="floating",
        pads=(
            ExpandedPad(
                component="R1",
                pin="PIN_1",
                abs_x=None,
                abs_y=None,
                orientation=None,
                local_x=-0.4,
                local_y=0.0,
                kind="smd",
            ),
            ExpandedPad(
                component="R1",
                pin="PIN_2",
                abs_x=None,
                abs_y=None,
                orientation=None,
                local_x=0.4,
                local_y=0.0,
                kind="smd",
            ),
        ),
        footprint_dims_mm=(1.0, 1.6),
    )
    return FrontendArtifact(
        project_name="delta",
        board={"width": 20.0, "height": 20.0},
        lint_report=LintReport(),
        components={"U1": fixed, "C1": f1, "R1": f2},
    )


def test_static_delta_matches_full_recompute() -> None:
    artifact = _artifact()
    cfg = FloatingPlacerConfig()
    state = {
        "C1": FloatingPlacement(x=10.0, y=10.0, rotation=0),
        "R1": FloatingPlacement(x=12.0, y=10.0, rotation=0),
    }
    moved = FloatingPlacement(x=11.2, y=10.8, rotation=90)
    connected_pairs = _build_connected_pairs(artifact)
    obstacles: list[tuple[float, float, float, float]] = []

    old_static = _static_energy_full(
        artifact=artifact,
        state=state,
        obstacles=obstacles,
        board_w=20.0,
        board_h=20.0,
        cfg=cfg,
        connected_pairs=connected_pairs,
    )
    delta = _static_delta_for_move(
        artifact=artifact,
        state=state,
        moved_name="C1",
        new_place=moved,
        obstacles=obstacles,
        board_w=20.0,
        board_h=20.0,
        cfg=cfg,
        connected_pairs=connected_pairs,
    )
    state_new = dict(state)
    state_new["C1"] = moved
    new_static = _static_energy_full(
        artifact=artifact,
        state=state_new,
        obstacles=obstacles,
        board_w=20.0,
        board_h=20.0,
        cfg=cfg,
        connected_pairs=connected_pairs,
    )
    assert old_static + delta == pytest.approx(new_static, abs=1e-9)
