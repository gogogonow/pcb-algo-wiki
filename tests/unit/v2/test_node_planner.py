"""Unit tests for the v2 node planner heuristic."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.compile import compile_layout
from solver.v2.node_planner import plan_node_positions

YAML_PATH = Path(__file__).resolve().parents[3] / "rf_layout_simplified.yaml"


@pytest.mark.skipif(not YAML_PATH.exists(), reason="PA yaml missing")
def test_plan_node_positions_assigns_unique_uv_seeds() -> None:
    artifact = compile_layout(str(YAML_PATH))
    plan = plan_node_positions(artifact, board_width_mm=40.0, board_height_mm=100.0)
    # Within each reference_net the seeds must be distinct so A* targets do
    # not collapse — but multiple UVs on the same fixed-pad-less net (e.g.
    # PWR_NET, where the centroid falls back to the board centre) may share
    # a seed across nets. Group seeds by net and check intra-net uniqueness.
    by_net: dict[str | None, list[tuple[float, float]]] = {}
    for uv_name, uv in artifact.uv_components.items():
        net = uv.uv_meta.reference_net if uv.uv_meta else None
        by_net.setdefault(net, []).append(plan.uv_anchor_seed[uv_name])
    for net, seeds in by_net.items():
        rounded = {(round(x, 3), round(y, 3)) for x, y in seeds}
        assert len(rounded) == len(seeds), (
            f"UV seeds collide within net {net!r}: {seeds}"
        )

    # Every endpoint string referenced by edges should resolve to a position.
    for edge in artifact.edges.values():
        for ep in edge.connections:
            assert ep in plan.endpoint_xy, f"unresolved endpoint {ep}"


@pytest.mark.skipif(not YAML_PATH.exists(), reason="PA yaml missing")
def test_node_planner_respects_target_length_constraint() -> None:
    """Junction nodes must lie within target_length × tol of every constrained
    incident edge's other endpoint. Regression for IC1_pin1_seg2_end_split_pad
    which previously landed ~24 mm from its source despite a 1.29 mm target.
    """
    import math

    artifact = compile_layout(str(YAML_PATH))
    plan = plan_node_positions(artifact, board_width_mm=40.0, board_height_mm=100.0)

    tol = 1.5  # planner tol is 1.10; allow slack for board-clamping edge cases
    for node_name in artifact.nodes:
        node_xy = plan.endpoint_xy[node_name]
        for edge in artifact.edges.values():
            if node_name not in edge.connections:
                continue
            if edge.target_length is None or edge.target_length <= 0:
                continue
            other = next(ep for ep in edge.connections if ep != node_name)
            other_xy = plan.endpoint_xy.get(other)
            if other_xy is None:
                continue
            d = math.hypot(node_xy[0] - other_xy[0], node_xy[1] - other_xy[1])
            assert d <= edge.target_length * tol + 0.05, (
                f"node {node_name} is {d:.2f}mm from {other} but edge "
                f"{edge.name} has target_length={edge.target_length}mm"
            )
