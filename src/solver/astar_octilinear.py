"""M9 — Octilinear (8-direction) A* router.

Searches an obstacle-aware µm grid for a polyline connecting ``start`` to
``goal`` using the 8 cardinal+diagonal moves. Diagonal cost is ``√2``;
turning incurs a small extra penalty so the router prefers long straight
runs broken by single 45° miters (the visual hallmark of microwave layouts).

Length-locked edges (``rf_constrained_locked``): the search returns the
*shortest* obstacle-free polyline.  When that polyline is shorter than
``target − tol`` the M7 meander postproc pass adds detours later (per the
M9 design spec, §3 — meander is the locked-length fallback once the router
has guaranteed a crossing-free path). When it is *longer* than ``target +
tol`` the polyline is returned anyway with ``length_overshoot=True`` so the
orchestrator can flag the edge.

This module is intentionally independent of CP-SAT — it consumes only an
:class:`~solver.obstacle_map.ObstacleMap` plus start/goal coordinates in mm.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from schema.v6_ir import Point

from .obstacle_map import ObstacleMap
from .units import mm_to_um, um_to_mm

SQRT2 = math.sqrt(2.0)
DIRS_8: tuple[tuple[int, int], ...] = (
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
)


@dataclass(frozen=True)
class OctilinearConfig:
    turn_cost_45: float = 0.3
    turn_cost_90: float = 1.0
    max_expansions: int = 1_000_000
    endpoint_halo_cells: int = 2
    """Cell-radius around start/goal that is forced free (lets the trace exit pads)."""
    dir_lock_cells: int = 3
    """Grid cells from start/goal within which direction deviations are penalised."""
    dir_lock_penalty: float = 10.0
    """Extra cost added when moving against the locked direction within dir_lock_cells."""


@dataclass(frozen=True)
class OctilinearResult:
    polyline: tuple[Point, ...] | None
    length_um: int
    expansions: int
    length_overshoot: bool = False
    """True when polyline length exceeds an upstream target_length + tol."""


def _step_cost(direction: tuple[int, int]) -> float:
    return SQRT2 if direction[0] != 0 and direction[1] != 0 else 1.0


def _turn_extra_cost(
    prev: tuple[int, int] | None, cur: tuple[int, int], cfg: OctilinearConfig
) -> float:
    if prev is None or prev == cur:
        return 0.0
    # Angle between unit vectors via dot product on the {-1,0,1} grid.
    dot = prev[0] * cur[0] + prev[1] * cur[1]
    mag_p = math.hypot(prev[0], prev[1])
    mag_c = math.hypot(cur[0], cur[1])
    cos_a = dot / (mag_p * mag_c)
    cos_a = max(-1.0, min(1.0, cos_a))
    angle = math.degrees(math.acos(cos_a))
    if angle <= 1.0:
        return 0.0
    if angle <= 46.0:
        return cfg.turn_cost_45
    return cfg.turn_cost_90


def _dir_lock_extra(
    cell: tuple[int, int],
    direction: tuple[int, int],
    lock_dir: tuple[int, int] | None,
    ref_cell: tuple[int, int],
    lock_cells: int,
    penalty: float,
) -> float:
    if lock_dir is None or lock_cells <= 0:
        return 0.0
    dist = max(abs(cell[0] - ref_cell[0]), abs(cell[1] - ref_cell[1]))
    if dist > lock_cells:
        return 0.0
    dot = direction[0] * lock_dir[0] + direction[1] * lock_dir[1]
    if dot < 0:
        return penalty
    return 0.0


def _heuristic_octilinear(cell: tuple[int, int], goal: tuple[int, int]) -> float:
    dx = abs(cell[0] - goal[0])
    dy = abs(cell[1] - goal[1])
    return (dx + dy - min(dx, dy)) + min(dx, dy) * SQRT2


def _xy_to_cell(x_um: int, y_um: int, step: int) -> tuple[int, int]:
    return x_um // step, y_um // step


def _segment_length_um(a: tuple[int, int], b: tuple[int, int], step: int) -> int:
    dx = (b[0] - a[0]) * step
    dy = (b[1] - a[1]) * step
    return int(round(math.hypot(dx, dy)))


def _polyline_length_um(cells: list[tuple[int, int]], step: int) -> int:
    total = 0
    for i in range(len(cells) - 1):
        total += _segment_length_um(cells[i], cells[i + 1], step)
    return total


def _compress_cells(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop interior collinear cells so we emit minimum-segment polylines."""
    if len(cells) <= 2:
        return list(cells)
    out = [cells[0]]
    for i in range(1, len(cells) - 1):
        prev = out[-1]
        cur = cells[i]
        nxt = cells[i + 1]
        d1 = (cur[0] - prev[0], cur[1] - prev[1])
        d2 = (nxt[0] - cur[0], nxt[1] - cur[1])

        def _norm(d: tuple[int, int]) -> tuple[int, int]:
            if d == (0, 0):
                return d
            mag = max(abs(d[0]), abs(d[1]))
            return (d[0] // mag, d[1] // mag) if mag else d

        if _norm(d1) == _norm(d2):
            continue
        out.append(cur)
    out.append(cells[-1])
    return out


def search(
    *,
    start_xy_mm: tuple[float, float],
    goal_xy_mm: tuple[float, float],
    obstacles: ObstacleMap,
    config: OctilinearConfig | None = None,
    target_length_mm: float | None = None,
    length_tol_um: int = 0,
    start_dir: tuple[int, int] | None = None,
    end_dir: tuple[int, int] | None = None,
) -> OctilinearResult:
    """Run octilinear A* from ``start`` to ``goal`` and return the polyline.

    ``target_length_mm`` is informational — when supplied with ``length_tol_um``,
    the result's :attr:`OctilinearResult.length_overshoot` is set when the
    shortest polyline already exceeds the locked target.  Length *undershoot*
    is the responsibility of the meander postproc and is not flagged here.
    """

    cfg = config or OctilinearConfig()
    step = obstacles.step_um
    start_um = (mm_to_um(start_xy_mm[0]), mm_to_um(start_xy_mm[1]))
    goal_um = (mm_to_um(goal_xy_mm[0]), mm_to_um(goal_xy_mm[1]))
    start = _xy_to_cell(*start_um, step)
    goal = _xy_to_cell(*goal_um, step)

    # Force a small neighbourhood around start/goal to be free so the trace
    # can leave / enter the pad even if the pad halo blocked its own cell.
    free: set[tuple[int, int]] = set()
    halo = max(0, cfg.endpoint_halo_cells)
    for ax, ay in (start, goal):
        for dx in range(-halo, halo + 1):
            for dy in range(-halo, halo + 1):
                free.add((ax + dx, ay + dy))

    open_heap: list[tuple[float, int, tuple[int, int], tuple[int, int] | None]] = []
    counter = 0
    heapq.heappush(
        open_heap, (_heuristic_octilinear(start, goal), counter, start, None)
    )
    came_from: dict[tuple[int, int], tuple[tuple[int, int], tuple[int, int] | None]] = (
        {}
    )
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    expansions = 0

    while open_heap:
        if expansions >= cfg.max_expansions:
            break
        _, _, current, prev_dir = heapq.heappop(open_heap)
        expansions += 1
        if current == goal:
            cells = [current]
            node = current
            while node in came_from:
                parent, _ = came_from[node]
                cells.append(parent)
                node = parent
            cells.reverse()
            cells = _compress_cells(cells)
            length_um = _polyline_length_um(cells, step)
            polyline = tuple(
                Point(x=um_to_mm(c[0] * step), y=um_to_mm(c[1] * step)) for c in cells
            )
            # Snap endpoints to the requested coordinates so we don't drift
            # by half a grid step.
            polyline = (
                (Point(x=start_xy_mm[0], y=start_xy_mm[1]),)
                + polyline[1:-1]
                + (Point(x=goal_xy_mm[0], y=goal_xy_mm[1]),)
            )
            overshoot = False
            if target_length_mm is not None:
                target_um = mm_to_um(target_length_mm)
                if length_um > target_um + length_tol_um:
                    overshoot = True
            return OctilinearResult(
                polyline=polyline,
                length_um=length_um,
                expansions=expansions,
                length_overshoot=overshoot,
            )

        for d in DIRS_8:
            nb = (current[0] + d[0], current[1] + d[1])
            if not obstacles.bounds.contains(nb):
                continue
            if nb in obstacles.obstacles and nb not in free:
                continue
            cost = (
                _step_cost(d)
                + _turn_extra_cost(prev_dir, d, cfg)
                + _dir_lock_extra(
                    nb, d, start_dir, start, cfg.dir_lock_cells, cfg.dir_lock_penalty
                )
                + _dir_lock_extra(
                    nb,
                    d,
                    (-end_dir[0], -end_dir[1]) if end_dir is not None else None,
                    goal,
                    cfg.dir_lock_cells,
                    cfg.dir_lock_penalty,
                )
            )
            tentative = g_score[current] + cost
            if tentative < g_score.get(nb, float("inf")):
                g_score[nb] = tentative
                came_from[nb] = (current, d)
                counter += 1
                heapq.heappush(
                    open_heap,
                    (tentative + _heuristic_octilinear(nb, goal), counter, nb, d),
                )

    return OctilinearResult(
        polyline=None,
        length_um=0,
        expansions=expansions,
        length_overshoot=False,
    )


__all__ = [
    "DIRS_8",
    "OctilinearConfig",
    "OctilinearResult",
    "search",
]
