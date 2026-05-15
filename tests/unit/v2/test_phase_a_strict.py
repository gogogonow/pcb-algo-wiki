"""Strict invariants for Phase A 45°-only routing (WI-A3..A5)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from solver.v2 import solve_layout_v2

YAML_PATH = Path(__file__).resolve().parents[3] / "rf_layout_simplified.yaml"


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    """Strict segment-segment intersection (no shared-endpoint tolerance).

    Returns True only when the open segments cross. Touching endpoints (any
    end of A coincides with any end of B) are NOT counted, since shared
    junction nodes are legitimate.
    """

    def ccw(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    # Skip if endpoints coincide (shared junction).
    eps = 1e-3
    for ea in (a1, a2):
        for eb in (b1, b2):
            if math.hypot(ea[0] - eb[0], ea[1] - eb[1]) < eps:
                return False

    d1 = ccw(b1, b2, a1)
    d2 = ccw(b1, b2, a2)
    d3 = ccw(a1, a2, b1)
    d4 = ccw(a1, a2, b2)
    if (d1 * d2 < 0) and (d3 * d4 < 0):
        return True
    return False


@pytest.mark.skipif(not YAML_PATH.exists(), reason="PA yaml missing")
def test_phase_a_routes_use_only_45deg_directions() -> None:
    """Every successful route segment must be along one of 8 axial / diagonal
    directions (multiples of 45°)."""
    result = solve_layout_v2(str(YAML_PATH))
    for edge_id, route in result.phase_a.skeleton.routes.items():
        if not route.success or len(route.polyline_um) < 2:
            continue
        for (ax, ay), (bx, by) in zip(route.polyline_um, route.polyline_um[1:]):
            dx = bx - ax
            dy = by - ay
            if dx == 0 and dy == 0:
                continue
            # Allowed: dx==0, dy==0, or |dx|==|dy| (axis-aligned or 45°).
            assert dx == 0 or dy == 0 or abs(dx) == abs(dy), (
                f"{edge_id} segment {(ax, ay)}->{(bx, by)} not on 45° lattice"
            )


@pytest.mark.skipif(not YAML_PATH.exists(), reason="PA yaml missing")
def test_phase_a_no_orthogonal_corners() -> None:
    """After WI-A3 chamfering, no two consecutive segments should form a 90°
    corner (every 90° turn must be split into two 45° turns)."""
    result = solve_layout_v2(str(YAML_PATH))
    for edge_id, route in result.phase_a.skeleton.routes.items():
        if not route.success or len(route.polyline_um) < 3:
            continue
        pts = route.polyline_um
        for i in range(1, len(pts) - 1):
            ax, ay = pts[i - 1]
            bx, by = pts[i]
            cx, cy = pts[i + 1]
            d1 = math.hypot(bx - ax, by - ay)
            d2 = math.hypot(cx - bx, cy - by)
            if d1 < 1e-6 or d2 < 1e-6:
                continue
            ux = (bx - ax) / d1
            uy = (by - ay) / d1
            vx = (cx - bx) / d2
            vy = (cy - by) / d2
            dot = ux * vx + uy * vy
            # 90° => dot == 0 (within rounding); chamfer must avoid this.
            assert abs(dot) > 0.05, (
                f"{edge_id} has un-chamfered 90° corner near "
                f"index {i} = ({bx / 1000.0:.3f}, {by / 1000.0:.3f}) mm"
            )
