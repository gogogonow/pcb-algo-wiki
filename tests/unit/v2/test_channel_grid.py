"""Unit tests for the v2 octilinear A* (channel_grid)."""

from __future__ import annotations

from solver.v2.channel_grid import ChannelGrid, GridConfig, route_octilinear


def _grid(step: int = 100) -> ChannelGrid:
    return ChannelGrid(
        board_min=(0, 0),
        board_max=(20_000, 20_000),
        config=GridConfig(step_um=step, max_expansions=50_000),
    )


def test_route_straight_line_succeeds() -> None:
    grid = _grid()
    path = route_octilinear(grid, "e1", (0, 0), (5_000, 0))
    assert path.success
    assert path.points_um[0] == (0, 0)
    assert path.points_um[-1] == (5_000, 0)
    assert path.length_um == 5_000


def test_route_l_shape_uses_octilinear() -> None:
    grid = _grid()
    path = route_octilinear(grid, "e1", (0, 0), (3_000, 3_000))
    assert path.success
    # Should travel diagonally; length ~ 3000 * sqrt(2) ≈ 4242 (using 142 cost)
    assert 4_200 <= path.length_um <= 4_300
    assert path.points_um[0] == (0, 0)
    assert path.points_um[-1] == (3_000, 3_000)


def test_route_around_obstacle() -> None:
    grid = _grid()
    grid.add_fixed_aabb(2_000, -1_000, 4_000, 1_000, "wall")
    path = route_octilinear(grid, "e1", (0, 0), (6_000, 0))
    assert path.success
    # Path must detour around the wall — endpoints intact.
    assert path.points_um[0] == (0, 0)
    assert path.points_um[-1] == (6_000, 0)
    # Not the straight-line length.
    assert path.length_um > 6_000


def test_route_blocked_start_returns_failure() -> None:
    grid = _grid()
    grid.add_fixed_aabb(-2_000, -2_000, 2_000, 2_000, "wall")
    path = route_octilinear(grid, "e1", (0, 0), (10_000, 10_000))
    # Snap-to-free should relocate the start.
    assert path.success or "blocked" in (path.failure_reason or "")


def test_ignore_label_lets_search_pass() -> None:
    grid = _grid()
    grid.add_fixed_aabb(2_000, -1_000, 4_000, 1_000, "footprint:U1")
    blocked = route_octilinear(grid, "e1", (0, 0), (6_000, 0))
    # Without ignore: snap-to-free still finds something.
    assert blocked.success
    # With ignore: should be cheaper (straight or near-straight).
    cleared = route_octilinear(
        grid, "e2", (0, 0), (6_000, 0), ignore_labels=("footprint:U1",)
    )
    assert cleared.success
    assert cleared.length_um <= blocked.length_um


def test_remove_routed_clears_obstacle() -> None:
    grid = _grid()
    grid.add_routed_polyline(
        ((2_000, -2_000), (2_000, 2_000)), width_um=400, clearance_um=100, label="r1"
    )
    blocked = route_octilinear(grid, "e1", (0, 0), (4_000, 0))
    assert blocked.success
    detour_len = blocked.length_um
    removed = grid.remove_routed("r1")
    assert removed >= 1
    cleared = route_octilinear(grid, "e2", (0, 0), (4_000, 0))
    assert cleared.length_um <= detour_len
