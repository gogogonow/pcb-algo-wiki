"""Octilinear A* on a uniform µm grid (Phase A core search).

Coordinates are stored as integer micrometres throughout; the caller is
responsible for converting from / to millimetres.

Obstacles are recorded as inflated axis-aligned bounding boxes (AABBs) plus
inflated polylines (already-routed RF segments). The grid is *implicit*: we
hash blocked cells into a set and check on demand, which avoids materialising
a 400 000-cell array for the 40×100 mm board.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from solver.units import MM_TO_UM

# 8 octilinear neighbours: (dx_in_steps, dy_in_steps, base_cost_factor_x100)
# Actual cost = base_cost_factor * step_um // 100. Diagonals = √2 ≈ 1.414.
_DIRECTIONS = (
    (1, 0, 100),
    (-1, 0, 100),
    (0, 1, 100),
    (0, -1, 100),
    (1, 1, 142),
    (1, -1, 142),
    (-1, 1, 142),
    (-1, -1, 142),
)


@dataclass(frozen=True)
class GridConfig:
    """Grid + cost weights used by :func:`route_octilinear`."""

    step_um: int = 200
    turn_penalty_um: int = 100
    near_obstacle_penalty_um: int = 40
    max_expansions: int = 40_000


@dataclass(frozen=True)
class RoutedPath:
    """Result of a single A* search."""

    edge_id: str
    points_um: tuple[tuple[int, int], ...]
    length_um: int
    expansions: int
    success: bool
    failure_reason: str | None = None

    def length_mm(self) -> float:
        return self.length_um / float(MM_TO_UM)

    def points_mm(self) -> tuple[tuple[float, float], ...]:
        return tuple((x / MM_TO_UM, y / MM_TO_UM) for x, y in self.points_um)


@dataclass
class ChannelGrid:
    """Implicit grid + obstacle book-keeping shared across edge routings.

    Obstacles are stored as inflated AABBs with an integer "id" so the caller
    can rip-up a specific edge later (e.g. when a target endpoint is
    blocked).
    """

    board_min: tuple[int, int]
    board_max: tuple[int, int]
    config: GridConfig = field(default_factory=GridConfig)
    fixed_obstacles: list[tuple[int, int, int, int, str]] = field(default_factory=list)
    routed_obstacles: list[tuple[int, int, int, int, str]] = field(default_factory=list)
    _bucket_size: int = 2000  # 2 mm buckets
    _fixed_buckets: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    _routed_buckets: dict[tuple[int, int], list[int]] = field(default_factory=dict)

    def _bucketize(
        self,
        bucket_map: dict[tuple[int, int], list[int]],
        idx: int,
        aabb: tuple[int, int, int, int, str],
    ) -> None:
        x0, y0, x1, y1, _ = aabb
        bs = self._bucket_size
        for bx in range(x0 // bs, x1 // bs + 1):
            for by in range(y0 // bs, y1 // bs + 1):
                bucket_map.setdefault((bx, by), []).append(idx)

    def add_fixed_aabb(
        self, min_x: int, min_y: int, max_x: int, max_y: int, label: str
    ) -> None:
        idx = len(self.fixed_obstacles)
        aabb = (min_x, min_y, max_x, max_y, label)
        self.fixed_obstacles.append(aabb)
        self._bucketize(self._fixed_buckets, idx, aabb)

    def add_routed_polyline(
        self,
        points: tuple[tuple[int, int], ...],
        width_um: int,
        clearance_um: int,
        label: str,
    ) -> None:
        """Inflate each polyline segment to an AABB and store it as obstacle.

        Diagonal segments are split into shorter sub-segments so the per-AABB
        bounding box stays tight around the actual trace. Without splitting,
        a long 45° segment's AABB covers the entire enclosing square,
        falsely blocking nearby parallel routes.
        """
        inflate = width_um // 2 + clearance_um
        # Cap each sub-segment length so AABB stays close to trace width.
        chunk_um = max(width_um * 2, 1000)
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            dx = x2 - x1
            dy = y2 - y1
            is_diagonal = dx != 0 and dy != 0
            seg_len = max(abs(dx), abs(dy))
            n_chunks = (
                max(1, (seg_len + chunk_um - 1) // chunk_um) if is_diagonal else 1
            )
            for k in range(n_chunks):
                t0 = k / n_chunks
                t1 = (k + 1) / n_chunks
                sx = x1 + int(dx * t0)
                sy = y1 + int(dy * t0)
                ex = x1 + int(dx * t1)
                ey = y1 + int(dy * t1)
                min_x = min(sx, ex) - inflate
                max_x = max(sx, ex) + inflate
                min_y = min(sy, ey) - inflate
                max_y = max(sy, ey) + inflate
                idx = len(self.routed_obstacles)
                aabb = (min_x, min_y, max_x, max_y, label)
                self.routed_obstacles.append(aabb)
                self._bucketize(self._routed_buckets, idx, aabb)

    def remove_routed(self, label: str) -> int:
        before = len(self.routed_obstacles)
        keep = [o for o in self.routed_obstacles if o[4] != label]
        removed = before - len(keep)
        self.routed_obstacles = keep
        # Rebuild bucket index.
        self._routed_buckets = {}
        for idx, aabb in enumerate(self.routed_obstacles):
            self._bucketize(self._routed_buckets, idx, aabb)
        return removed

    def is_blocked(
        self,
        x: int,
        y: int,
        ignore_labels: tuple[str, ...] = (),
    ) -> bool:
        if x < self.board_min[0] or x > self.board_max[0]:
            return True
        if y < self.board_min[1] or y > self.board_max[1]:
            return True
        bs = self._bucket_size
        bx, by = x // bs, y // bs
        for idx in self._fixed_buckets.get((bx, by), ()):
            ox0, oy0, ox1, oy1, label = self.fixed_obstacles[idx]
            if label in ignore_labels:
                continue
            if ox0 <= x <= ox1 and oy0 <= y <= oy1:
                return True
        for idx in self._routed_buckets.get((bx, by), ()):
            ox0, oy0, ox1, oy1, label = self.routed_obstacles[idx]
            if label in ignore_labels:
                continue
            if ox0 <= x <= ox1 and oy0 <= y <= oy1:
                return True
        return False


def _snap_to_nearest_free(
    grid: ChannelGrid,
    x: int,
    y: int,
    step: int,
    ignore_labels: tuple[str, ...],
    max_radius: int = 20,
) -> tuple[int, int] | None:
    """Spiral-search for the nearest unblocked grid cell."""
    for r in range(1, max_radius + 1):
        for dx in range(-r, r + 1):
            for dy in (-r, r):
                nx = x + dx * step
                ny = y + dy * step
                if not grid.is_blocked(nx, ny, ignore_labels=ignore_labels):
                    return (nx, ny)
            for dy in range(-r + 1, r):
                for sx in (-r, r):
                    nx = x + sx * step
                    ny = y + dy * step
                    if not grid.is_blocked(nx, ny, ignore_labels=ignore_labels):
                        return (nx, ny)
    return None


def _snap(value: int, step: int) -> int:
    """Snap to nearest grid multiple."""
    return int(round(value / step)) * step


def route_octilinear(
    grid: ChannelGrid,
    edge_id: str,
    start_um: tuple[int, int],
    goal_um: tuple[int, int],
    *,
    ignore_labels: tuple[str, ...] = (),
    max_length_um: int | None = None,
) -> RoutedPath:
    """Single-edge 8-direction A* with turn-penalty and length upper bound.

    Both ``ignore_labels`` (so a search may pass through its own footprint
    halo or a previously-routed edge being rip-up-replaced) and
    ``max_length_um`` are honoured.
    """
    step = grid.config.step_um
    sx = _snap(start_um[0], step)
    sy = _snap(start_um[1], step)
    gx = _snap(goal_um[0], step)
    gy = _snap(goal_um[1], step)

    if grid.is_blocked(sx, sy, ignore_labels=ignore_labels) and (sx, sy) != (gx, gy):
        relocated = _snap_to_nearest_free(grid, sx, sy, step, ignore_labels)
        if relocated is None:
            return RoutedPath(
                edge_id=edge_id,
                points_um=(),
                length_um=0,
                expansions=0,
                success=False,
                failure_reason=f"start ({sx},{sy}) is blocked",
            )
        sx, sy = relocated
    if grid.is_blocked(gx, gy, ignore_labels=ignore_labels) and (sx, sy) != (gx, gy):
        relocated = _snap_to_nearest_free(grid, gx, gy, step, ignore_labels)
        if relocated is not None:
            gx, gy = relocated

    # Heuristic: octilinear distance (step-aware).
    diag = (142 * step) // 100
    ortho = step

    def h(x: int, y: int) -> int:
        dx = abs(x - gx)
        dy = abs(y - gy)
        d_min = min(dx, dy)
        d_max = max(dx, dy)
        return diag * (d_min // step) + ortho * ((d_max - d_min) // step)

    # State: (x, y, last_direction_idx_or_-1)
    # Heap entries carry the full state directly (x, y, dir_idx) so that
    # multiple states at the same (x, y) with different incoming directions
    # remain independently retrievable. The earlier implementation stored
    # only (x, y) in the heap and recovered "the latest" state through a
    # lookup table — which silently dropped expansions whenever a cell was
    # reached twice from different directions and caused A* to give up
    # well before exhausting the search space.
    open_heap: list[tuple[int, int, int, int, int, int]] = []
    start_state = (sx, sy, -1)
    g_score: dict[tuple[int, int, int], int] = {start_state: 0}
    came_from: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    counter = 0
    heapq.heappush(open_heap, (h(sx, sy), counter, sx, sy, -1, 0))

    expansions = 0
    closed: set[tuple[int, int, int]] = set()

    while open_heap:
        if expansions >= grid.config.max_expansions:
            return RoutedPath(
                edge_id=edge_id,
                points_um=(),
                length_um=0,
                expansions=expansions,
                success=False,
                failure_reason="max_expansions exhausted",
            )
        _f, _ctr, x, y, last_dir, _g = heapq.heappop(open_heap)
        state = (x, y, last_dir)
        if state in closed:
            continue
        closed.add(state)
        expansions += 1

        if (x, y) == (gx, gy):
            return _reconstruct(
                came_from, state, edge_id, expansions, step, max_length_um
            )

        cur_g = g_score[state]
        for idx, (dx, dy, cost_factor) in enumerate(_DIRECTIONS):
            nx = x + dx * step
            ny = y + dy * step
            if grid.is_blocked(nx, ny, ignore_labels=ignore_labels):
                continue
            tentative = cur_g + (cost_factor * step) // 100
            if last_dir != -1 and last_dir != idx:
                tentative += grid.config.turn_penalty_um
            if max_length_um is not None and tentative > max_length_um:
                continue
            new_state = (nx, ny, idx)
            if new_state in closed:
                continue
            prev_g = g_score.get(new_state)
            if prev_g is not None and prev_g <= tentative:
                continue
            g_score[new_state] = tentative
            came_from[new_state] = state
            counter += 1
            heapq.heappush(
                open_heap, (tentative + h(nx, ny), counter, nx, ny, idx, tentative)
            )

    return RoutedPath(
        edge_id=edge_id,
        points_um=(),
        length_um=0,
        expansions=expansions,
        success=False,
        failure_reason="no path",
    )


def _reconstruct(
    came_from: dict[tuple[int, int, int], tuple[int, int, int]],
    final_state: tuple[int, int, int],
    edge_id: str,
    expansions: int,
    step: int,
    max_length_um: int | None,
) -> RoutedPath:
    pts: list[tuple[int, int]] = []
    state = final_state
    while True:
        pts.append((state[0], state[1]))
        parent = came_from.get(state)
        if parent is None:
            break
        state = parent
    pts.reverse()
    # Simplify collinear points (keep only direction changes).
    simplified: list[tuple[int, int]] = [pts[0]]
    for i in range(1, len(pts) - 1):
        x0, y0 = simplified[-1]
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx_a, dy_a = x1 - x0, y1 - y0
        dx_b, dy_b = x2 - x1, y2 - y1
        # Same direction if cross product == 0 and same sign on dot product.
        if dx_a * dy_b - dy_a * dx_b == 0 and (dx_a * dx_b + dy_a * dy_b) > 0:
            continue
        simplified.append(pts[i])
    if len(pts) >= 2:
        simplified.append(pts[-1])
    length_um = 0
    for (x1, y1), (x2, y2) in zip(simplified, simplified[1:], strict=False):
        length_um += int(round(math.hypot(x2 - x1, y2 - y1)))
    if max_length_um is not None and length_um > max_length_um:
        return RoutedPath(
            edge_id=edge_id,
            points_um=tuple(simplified),
            length_um=length_um,
            expansions=expansions,
            success=False,
            failure_reason=f"length {length_um} > max {max_length_um}",
        )
    return RoutedPath(
        edge_id=edge_id,
        points_um=tuple(simplified),
        length_um=length_um,
        expansions=expansions,
        success=True,
    )


__all__ = [
    "ChannelGrid",
    "GridConfig",
    "RoutedPath",
    "route_octilinear",
]
