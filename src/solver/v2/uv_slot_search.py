"""Generalized perpendicular-slot search for UV (RLC) component adhesion.

Given a UV component bound to a net, this module enumerates candidate
attach points along every routed microstrip belonging to that net, on
both sides (left/right of the trace direction), at a fixed sampling
step, and scores each by clearance to known obstacles (board frame,
other components, other routes). The best feasible candidate is
returned.

The algorithm is intentionally written in terms of primitives
(polylines, rectangles, footprint size) so it can be reused for any
case — it does **not** depend on PA-specific identifiers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Rect = tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)
Point = tuple[float, float]


@dataclass(frozen=True)
class SlotCandidate:
    edge_id: str
    t: float
    anchor_xy: Point  # final RLC body center (mm)
    side: int  # +1 (left of trace direction) / -1 (right)
    rotation_deg: float  # quantised {0, 90, 180, -90}
    clearance: float  # min distance from RLC bbox to nearest obstacle (mm)
    host_pin_xy: Point  # point on host trace where anchor pin attaches
    trace_width: float = 0.5  # WI-G2: host trace width (mm)


def _polyline_length(poly: list[Point]) -> float:
    length = 0.0
    for i in range(len(poly) - 1):
        length += math.hypot(poly[i + 1][0] - poly[i][0], poly[i + 1][1] - poly[i][1])
    return length


def _sample_polyline(poly: list[Point], step: float) -> list[tuple[Point, Point]]:
    """Sample (point, unit_tangent) pairs along the polyline at step intervals."""
    out: list[tuple[Point, Point]] = []
    if len(poly) < 2:
        return out
    total = _polyline_length(poly)
    if total <= 0:
        return out
    # WI-F3: ensure short edges still produce ≥1 interior sample; previously
    # n = max(1, int(total/step)) yielded only the two endpoints for any edge
    # shorter than `step`, silently disabling slot search for short host
    # traces (e.g. IC1_pin1_seg2 at 1.3 mm with step 0.8 mm → 0 candidates →
    # C7 fell back to legacy anchor with rot=0).
    n = max(2, int(total / step))
    distances = [i * total / n for i in range(n + 1)]
    seg_idx = 0
    seg_start_dist = 0.0
    seg_len = math.hypot(poly[1][0] - poly[0][0], poly[1][1] - poly[0][1])
    for d in distances:
        while seg_idx < len(poly) - 2 and d > seg_start_dist + seg_len:
            seg_start_dist += seg_len
            seg_idx += 1
            seg_len = math.hypot(
                poly[seg_idx + 1][0] - poly[seg_idx][0],
                poly[seg_idx + 1][1] - poly[seg_idx][1],
            )
        if seg_len <= 0:
            continue
        local = (d - seg_start_dist) / seg_len
        local = max(0.0, min(1.0, local))
        x = poly[seg_idx][0] + (poly[seg_idx + 1][0] - poly[seg_idx][0]) * local
        y = poly[seg_idx][1] + (poly[seg_idx + 1][1] - poly[seg_idx][1]) * local
        tx = (poly[seg_idx + 1][0] - poly[seg_idx][0]) / seg_len
        ty = (poly[seg_idx + 1][1] - poly[seg_idx][1]) / seg_len
        out.append(((x, y), (tx, ty)))
    return out


def _quantize_rotation(deg: float) -> float:
    allowed = (0.0, 90.0, 180.0, -90.0)
    a = ((deg + 180.0) % 360.0) - 180.0

    def diff(x: float, y: float) -> float:
        return abs(((x - y + 180.0) % 360.0) - 180.0)

    return float(min(allowed, key=lambda v: diff(a, v)))


def _rect_distance(r1: Rect, r2: Rect) -> float:
    """0 if rects overlap; otherwise the minimum gap (mm)."""
    dx = max(r1[0] - r2[2], r2[0] - r1[2], 0.0)
    dy = max(r1[1] - r2[3], r2[1] - r1[3], 0.0)
    return math.hypot(dx, dy)


def _rect_seg_distance(rect: Rect, p1: Point, p2: Point, half_width: float) -> float:
    """Distance between an AABB and a thick segment (swept by half_width).
    Returns 0 if they overlap.
    """
    sx = min(p1[0], p2[0]) - half_width
    sy = min(p1[1], p2[1]) - half_width
    ex = max(p1[0], p2[0]) + half_width
    ey = max(p1[1], p2[1]) + half_width
    return _rect_distance(rect, (sx, sy, ex, ey))


def _bbox_inside(rect: Rect, board: tuple[float, float]) -> bool:
    return (
        rect[0] >= 0.0
        and rect[1] >= 0.0
        and rect[2] <= board[0]
        and rect[3] <= board[1]
    )


def search_slot(
    *,
    footprint_size: tuple[float, float],
    anchor_pin_local: tuple[float, float],
    host_polylines: dict[str, list[Point]],
    host_widths: dict[str, float],
    board: tuple[float, float],
    obstacles: list[Rect],
    step_mm: float = 1.5,
    min_clearance: float = 0.3,
) -> SlotCandidate | None:
    """Find the best perpendicular slot for a UV component.

    The RLC's ``anchor_pin`` is placed AT a sampled point on a host
    microstrip; the body extends perpendicular to the trace direction
    such that the body's long axis (footprint width) aligns with the
    perpendicular. ``side=+1`` places the body to the LEFT of the trace
    travel direction, ``side=-1`` to the RIGHT.

    Returns the highest-scoring feasible candidate, or None.
    """
    fw, fh = footprint_size
    alx, aly = anchor_pin_local
    candidates: list[SlotCandidate] = []
    for edge_id in sorted(host_polylines.keys()):
        poly = host_polylines[edge_id]
        if len(poly) < 2:
            continue
        samples = _sample_polyline(poly, step_mm)
        for i, (pt, tangent) in enumerate(samples):
            if i == 0 or i == len(samples) - 1:
                continue
            tx, ty = tangent
            for side in (1, -1):
                # perpendicular unit vectors (left=+1, right=-1 of travel)
                px, py = -ty * side, tx * side
                # Rotation aligns local-x axis with the perpendicular.
                rot = _quantize_rotation(math.degrees(math.atan2(py, px)))
                cos_t = math.cos(math.radians(rot))
                sin_t = math.sin(math.radians(rot))
                # Body origin such that anchor pin lands on pt.
                origin_x = pt[0] - (cos_t * alx - sin_t * aly)
                origin_y = pt[1] - (sin_t * alx + cos_t * aly)
                # Body bbox (axis-aligned around origin in local frame).
                # Local body bbox = [-fw/2, fw/2] x [-fh/2, fh/2]. Rotated.
                if int(round(rot)) % 180 == 0:
                    bw, bh = fw, fh
                else:
                    bw, bh = fh, fw
                rect = (
                    origin_x - bw / 2.0,
                    origin_y - bh / 2.0,
                    origin_x + bw / 2.0,
                    origin_y + bh / 2.0,
                )
                if not _bbox_inside(rect, board):
                    continue
                # Anchor pin sits on the trace centerline — relax board
                # frame check at the pin point: rect-vs-trace check below
                # would otherwise mark every candidate blocked. We
                # subtract a tiny epsilon from the host edge clearance
                # check for the host edge.
                clr = math.inf
                blocked = False
                for ob in obstacles:
                    d = _rect_distance(rect, ob)
                    if d < min_clearance:
                        blocked = True
                        break
                    clr = min(clr, d)
                if blocked:
                    continue
                # Clearance to OTHER host polylines (not self) and to
                # OWN polyline excluding the connection region around pt.
                for other_eid, other_poly in host_polylines.items():
                    other_hw = host_widths.get(other_eid, 0.0) / 2.0
                    for j in range(len(other_poly) - 1):
                        a_pt = other_poly[j]
                        b_pt = other_poly[j + 1]
                        if other_eid == edge_id:
                            seg_dist_to_pt = _point_seg_distance(pt, a_pt, b_pt)
                            if seg_dist_to_pt < 1e-6:
                                # Connection segment: only block if the body
                                # overlaps portions of the trace BEYOND the
                                # connection buffer.  Do NOT include these
                                # distances in the clearance score — it is
                                # expected that the body sits right next to
                                # the trace at the attachment point.
                                conn_buf = max(fw, fh) / 2.0 + other_hw + min_clearance
                                axb = b_pt[0] - a_pt[0]
                                ayb = b_pt[1] - a_pt[1]
                                seg_len_j = math.hypot(axb, ayb)
                                if seg_len_j < 1e-6:
                                    continue
                                t_pt = max(
                                    0.0,
                                    min(
                                        1.0,
                                        (
                                            (pt[0] - a_pt[0]) * axb
                                            + (pt[1] - a_pt[1]) * ayb
                                        )
                                        / (seg_len_j * seg_len_j),
                                    ),
                                )
                                t_buf = conn_buf / seg_len_j
                                # Sub-segment before pt (block only)
                                t1 = max(0.0, t_pt - t_buf)
                                if t1 > 1e-3:
                                    p1 = (a_pt[0] + t1 * axb, a_pt[1] + t1 * ayb)
                                    if (
                                        _rect_seg_distance(rect, a_pt, p1, other_hw)
                                        < min_clearance
                                    ):
                                        blocked = True
                                        break
                                # Sub-segment after pt (block only)
                                t2 = min(1.0, t_pt + t_buf)
                                if t2 < 1.0 - 1e-3:
                                    p2 = (a_pt[0] + t2 * axb, a_pt[1] + t2 * ayb)
                                    if (
                                        _rect_seg_distance(rect, p2, b_pt, other_hw)
                                        < min_clearance
                                    ):
                                        blocked = True
                                        break
                                continue
                        d = _rect_seg_distance(rect, a_pt, b_pt, other_hw)
                        if d < min_clearance:
                            blocked = True
                            break
                        clr = min(clr, d)
                    if blocked:
                        break
                if blocked:
                    continue
                if clr == math.inf:
                    clr = 1e6
                length = _polyline_length(poly)
                t = (i / max(1, len(samples) - 1)) if length > 0 else 0.0
                candidates.append(
                    SlotCandidate(
                        edge_id=edge_id,
                        t=t,
                        anchor_xy=(origin_x, origin_y),
                        side=side,
                        rotation_deg=rot,
                        clearance=clr,
                        host_pin_xy=pt,
                        trace_width=float(host_widths.get(edge_id, 0.5)),
                    )
                )
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c.clearance, c.edge_id, c.t, -c.side))
    return candidates[0]


def _point_seg_distance(p: Point, a: Point, b: Point) -> float:
    """Distance from point p to segment ab."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(p[0] - cx, p[1] - cy)


__all__ = ["SlotCandidate", "search_slot"]
