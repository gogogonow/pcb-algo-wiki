"""WI-J3 — Floating placer integration test."""

from __future__ import annotations

from solver.v2.floating_placer import (
    FloatingPlacement,
    FloatingPlacerConfig,
    place_floating_components,
)
from solver.v2.orchestrator import solve_layout_v2


def _bbox(comp_w: float, comp_h: float, p: FloatingPlacement) -> tuple:
    if p.rotation in (90, -90):
        comp_w, comp_h = comp_h, comp_w
    return (p.x - comp_w / 2, p.y - comp_h / 2, p.x + comp_w / 2, p.y + comp_h / 2)


def _bb_overlap(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_floating_placer_keeps_components_inside_board_and_disjoint():
    """运行整条 phase A+B，再独立调用 floating_placer 验证落点合法。"""
    result = solve_layout_v2("rf_layout_simplified.yaml")
    artifact = result.artifact
    floating_names = [
        n for n, c in artifact.components.items() if c.placement_kind == "floating"
    ]
    assert len(floating_names) >= 3, "案例需包含 ≥3 个 floating 组件"

    placements = place_floating_components(
        artifact=artifact,
        skeleton=result.phase_a.skeleton,
        adhesion=result.phase_b.adhesion,
        board_w=float(artifact.board.get("width", 40.0)),
        board_h=float(artifact.board.get("height", 85.0)),
        config=FloatingPlacerConfig(seed=42),
    )
    assert set(placements.keys()) == set(floating_names)

    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 85.0))

    bboxes: dict[str, tuple] = {}
    for name in floating_names:
        p = placements[name]
        assert p.rotation in (0, 90, 180, -90)
        # 估算 bbox（与 floating_placer._comp_wh 一致：用 pads 包络）
        comp = artifact.components[name]
        xs = [float(pad.local_x or 0.0) for pad in comp.pads]
        ys = [float(pad.local_y or 0.0) for pad in comp.pads]
        half_pw = (
            max((float(pad.pad_width or 0.0) for pad in comp.pads), default=0.0) / 2
        )
        half_pl = (
            max((float(pad.pad_length or 0.0) for pad in comp.pads), default=0.0) / 2
        )
        cw = (max(xs) - min(xs)) + 2 * half_pl
        ch = (max(ys) - min(ys)) + 2 * half_pw
        bb = _bbox(cw, ch, p)
        bboxes[name] = bb
        # 板内
        assert bb[0] >= 0.0 and bb[1] >= 0.0, f"{name} 越界: {bb}"
        assert bb[2] <= board_w and bb[3] <= board_h, f"{name} 越界: {bb}"

    # 互不重叠
    names = list(bboxes.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert not _bb_overlap(
                bboxes[names[i]], bboxes[names[j]]
            ), f"{names[i]} 与 {names[j]} bbox 重叠: {bboxes[names[i]]} vs {bboxes[names[j]]}"


def test_floating_placer_empty_when_no_floating_components(tmp_path):
    """无 floating 组件的最小 fixture：应返回空 dict。"""
    from frontend.models import FrontendArtifact, LintReport

    artifact = FrontendArtifact(
        project_name="empty",
        board={"width": 10.0, "height": 10.0, "origin": {"x": 0, "y": 0}},
        lint_report=LintReport(),
    )
    from solver.v2.skeleton_router import SkeletonReport
    from solver.v2.uv_adhesion import UvAdhesionReport

    out = place_floating_components(
        artifact=artifact,
        skeleton=SkeletonReport(),
        adhesion=UvAdhesionReport(),
        board_w=10.0,
        board_h=10.0,
    )
    assert out == {}
