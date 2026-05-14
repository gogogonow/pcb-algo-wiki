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
