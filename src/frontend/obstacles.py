"""Build the obstacle polygon list (M2b helper).

Inputs:

* ``layout.global_constraints.board_outline`` (rectangle, mm)
* ``layout.global_constraints.keepout_zones`` (free-form polygons, passed
  through as-is when the structure is recognisable)
* Fixed-component bounding boxes from
  :func:`frontend.expand_components.expand_components`

Output: a tuple of :class:`Obstacle` records that downstream solvers
(M3 / M4) can feed into ``NoOverlap`` constraints.
"""

from __future__ import annotations

from typing import Any

from schema.v33 import V33Layout

from .models import ComponentExpansion, Obstacle


def build_obstacles(
    layout: V33Layout,
    component_expansions: dict[str, ComponentExpansion],
) -> tuple[Obstacle, ...]:
    obstacles: list[Obstacle] = []

    board = layout.global_constraints.board_outline
    if board is not None:
        origin_x = (
            board.origin.x
            if board.origin is not None and board.origin.x is not None
            else 0.0
        )
        origin_y = (
            board.origin.y
            if board.origin is not None and board.origin.y is not None
            else 0.0
        )
        if board.width is not None and board.height is not None:
            obstacles.append(
                Obstacle(
                    kind="board_outline",
                    label="board_outline",
                    polygon=(
                        (origin_x, origin_y),
                        (origin_x + board.width, origin_y),
                        (origin_x + board.width, origin_y + board.height),
                        (origin_x, origin_y + board.height),
                    ),
                )
            )

    for index, raw_zone in enumerate(layout.global_constraints.keepout_zones):
        polygon = _coerce_polygon(raw_zone)
        if polygon is not None:
            label = (
                str(raw_zone.get("name", f"keepout_{index}"))
                if isinstance(raw_zone, dict)
                else f"keepout_{index}"
            )
            obstacles.append(Obstacle(kind="keepout", label=label, polygon=polygon))

    for name, expansion in component_expansions.items():
        if expansion.placement_kind == "fixed" and expansion.bbox is not None:
            obstacles.append(
                Obstacle(
                    kind="footprint_bbox",
                    label=name,
                    polygon=expansion.bbox.as_polygon(),
                )
            )

    return tuple(obstacles)


def _coerce_polygon(raw: Any) -> tuple[tuple[float, float], ...] | None:
    if isinstance(raw, dict):
        polygon = raw.get("polygon") or raw.get("points")
        if isinstance(polygon, list):
            return _points_from_list(polygon)
        if "min_x" in raw and "min_y" in raw and "max_x" in raw and "max_y" in raw:
            return (
                (float(raw["min_x"]), float(raw["min_y"])),
                (float(raw["max_x"]), float(raw["min_y"])),
                (float(raw["max_x"]), float(raw["max_y"])),
                (float(raw["min_x"]), float(raw["max_y"])),
            )
    if isinstance(raw, list):
        return _points_from_list(raw)
    return None


def _points_from_list(items: list[Any]) -> tuple[tuple[float, float], ...] | None:
    points: list[tuple[float, float]] = []
    for item in items:
        if isinstance(item, dict) and "x" in item and "y" in item:
            points.append((float(item["x"]), float(item["y"])))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            points.append((float(item[0]), float(item[1])))
        else:
            return None
    return tuple(points) if points else None


__all__ = ["build_obstacles"]
