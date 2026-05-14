"""M6 bend rendering — turn straight-segment polylines into bend-styled geometry.

Each :class:`schema.geometry_ir.RoutePolyline` is the raw output of M4/M5: a
sequence of straight segments. The optional ``bend_style`` on
:class:`schema.solver_ir.SolverEdge` describes how every internal corner should
be rendered:

* ``square``  / unset — keep the corner as-is (sharp 90°).
* ``mitered_45``       — cut the corner with a 45° chamfer.
* ``rounded`` / ``curved`` — replace the corner with a polyline that
  approximates a quarter-circle arc.

Output remains a :class:`RoutePolyline` (multi-point polyline) so that the
existing SVG renderer needs no change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pydantic import StrictStr

from schema.geometry_ir import GeometryIR, RoutePolyline
from schema.solver_ir import SolverIR
from schema.v6_ir import Point


@dataclass(frozen=True)
class BendReport:
    bended_edges: tuple[str, ...] = ()
    skipped_edges: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class _BendAccumulator:
    bended: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def freeze(self) -> BendReport:
        return BendReport(
            bended_edges=tuple(self.bended),
            skipped_edges=tuple(self.skipped),
            warnings=tuple(self.warnings),
        )


_KNOWN_STYLES = {"square", "mitered_45", "rounded", "curved"}
_ARC_SEGMENTS = 6  # quarter-arc approximation segments


def apply_bends(geom: GeometryIR, ir: SolverIR) -> tuple[GeometryIR, BendReport]:
    """Return a new :class:`GeometryIR` whose routes carry rendered bends."""

    acc = _BendAccumulator()
    new_routes: dict[str, RoutePolyline] = {}
    for edge_id, route in geom.routes.items():
        edge = ir.edges.get(edge_id)
        style = (edge.bend_style if edge is not None else None) or "square"
        if style not in _KNOWN_STYLES:
            acc.warnings.append(
                f"edge {edge_id!r}: unknown bend_style {style!r} → fallback to square"
            )
            style = "square"

        new_points = _render_polyline(route.points, style, float(route.width))
        if new_points == route.points:
            acc.skipped.append(edge_id)
        else:
            acc.bended.append(edge_id)
        new_routes[edge_id] = RoutePolyline(
            edge_id=route.edge_id,
            routing_class=route.routing_class,
            width=route.width,
            points=new_points,
        )

    bended_geom = geom.model_copy(update={"routes": new_routes})
    return bended_geom, acc.freeze()


def _render_polyline(
    points: tuple[Point, ...], style: StrictStr, width: float
) -> tuple[Point, ...]:
    if len(points) < 3 or style == "square":
        return points

    out: list[Point] = [points[0]]
    for i in range(1, len(points) - 1):
        prev_p = points[i - 1]
        cur_p = points[i]
        next_p = points[i + 1]
        if not _is_corner(prev_p, cur_p, next_p):
            out.append(cur_p)
            continue

        if style == "mitered_45":
            out.extend(_mitered_45(prev_p, cur_p, next_p, width))
        elif style in ("rounded", "curved"):
            out.extend(_rounded(prev_p, cur_p, next_p, width))
        else:  # pragma: no cover — guarded above
            out.append(cur_p)
    out.append(points[-1])
    return tuple(out)


def _is_corner(a: Point, b: Point, c: Point) -> bool:
    ax, ay = float(a.x), float(a.y)
    bx, by = float(b.x), float(b.y)
    cx, cy = float(c.x), float(c.y)
    # Cross product magnitude of (b-a) x (c-b); zero ⇒ collinear.
    cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
    return abs(cross) > 1e-9


def _segment_length(a: Point, b: Point) -> float:
    return math.hypot(float(b.x) - float(a.x), float(b.y) - float(a.y))


def _along(from_p: Point, to_p: Point, distance: float) -> Point:
    length = _segment_length(from_p, to_p)
    if length == 0.0:
        return Point(x=from_p.x, y=from_p.y)
    t = distance / length
    return Point(
        x=float(from_p.x) + (float(to_p.x) - float(from_p.x)) * t,
        y=float(from_p.y) + (float(to_p.y) - float(from_p.y)) * t,
    )


def _mitered_45(
    prev_p: Point, cur_p: Point, next_p: Point, width: float
) -> list[Point]:
    seg_a = _segment_length(prev_p, cur_p)
    seg_b = _segment_length(cur_p, next_p)
    chamfer = min(seg_a / 3.0, seg_b / 3.0, max(2.0 * width, width * 1.5))
    if chamfer <= 1e-9:
        return [cur_p]
    p_in = _along(cur_p, prev_p, chamfer)
    p_out = _along(cur_p, next_p, chamfer)
    return [p_in, p_out]


def _rounded(prev_p: Point, cur_p: Point, next_p: Point, width: float) -> list[Point]:
    seg_a = _segment_length(prev_p, cur_p)
    seg_b = _segment_length(cur_p, next_p)
    radius = min(seg_a / 3.0, seg_b / 3.0, max(2.0 * width, width * 1.5))
    if radius <= 1e-9:
        return [cur_p]

    p_in = _along(cur_p, prev_p, radius)
    p_out = _along(cur_p, next_p, radius)
    cx, cy = float(cur_p.x), float(cur_p.y)
    ax, ay = float(p_in.x) - cx, float(p_in.y) - cy
    bx, by = float(p_out.x) - cx, float(p_out.y) - cy

    arc: list[Point] = [p_in]
    for k in range(1, _ARC_SEGMENTS):
        t = k / _ARC_SEGMENTS
        # Spherical-linear interpolation degenerates to linear since both vectors
        # share length ``radius`` and span ≤ 90° in routing geometry.
        ix = ax * (1.0 - t) + bx * t
        iy = ay * (1.0 - t) + by * t
        norm = math.hypot(ix, iy)
        if norm == 0.0:
            continue
        scale = radius / norm
        arc.append(Point(x=cx + ix * scale, y=cy + iy * scale))
    arc.append(p_out)
    return arc


__all__ = ["BendReport", "apply_bends"]
