"""Render :class:`schema.geometry_ir.GeometryIR` to a static SVG.

Layered top-down: board → fixed footprint outlines → routes (stroked
polylines with width) → endpoint dots + labels.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from schema.geometry_ir import GeometryIR
from postproc.short_name import short_id

_FOOTPRINT_FILL = "#fde68a"
_FOOTPRINT_STROKE = "#92400e"
_ROUTE_STROKE = {
    "rf_constrained_locked": "#dc2626",
    "rf_unconstrained": "#2563eb",
    "rf_default": "#2563eb",
    "control_signal": "#16a34a",
    "power_signal": "#7c3aed",
}
_NODE_FILL = "#111827"


def render_geometry_svg(
    geom: GeometryIR,
    *,
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    board = geom.board
    bw = float(board.width) + 2 * margin_mm
    bh = float(board.height) + 2 * margin_mm

    def x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def y(mm: float) -> float:
        return (bh - (mm + margin_mm)) * px_per_mm  # SVG y flipped

    parts: list[str] = []
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {bw * px_per_mm:.1f} {bh * px_per_mm:.1f}" '
        f'width="{bw * px_per_mm:.1f}" height="{bh * px_per_mm:.1f}">'
    )
    parts.append('<rect x="0" y="0" width="100%" height="100%" fill="#f8fafc"/>')
    # Board outline.
    parts.append(
        f'<rect x="{x(0):.2f}" y="{y(float(board.height)):.2f}" '
        f'width="{float(board.width) * px_per_mm:.2f}" '
        f'height="{float(board.height) * px_per_mm:.2f}" '
        'fill="#ffffff" stroke="#475569" stroke-width="1.2"/>'
    )

    # Fixed component footprints (drawn from artifact extents via pad bounds).
    for placement in geom.placements.values():
        if not placement.pads:
            continue
        xs = [float(p.point.x) for p in placement.pads]
        ys = [float(p.point.y) for p in placement.pads]
        if max(xs) == min(xs) or max(ys) == min(ys):
            # zero-area testpoint or single-pad component
            for pad in placement.pads:
                parts.append(
                    f'<circle cx="{x(float(pad.point.x)):.2f}" '
                    f'cy="{y(float(pad.point.y)):.2f}" r="2" '
                    f'fill="{_FOOTPRINT_STROKE}"/>'
                )
            anchor_x = x(float(placement.anchor.x))
            anchor_y = y(float(placement.anchor.y))
            parts.append(
                f'<text x="{anchor_x + 3:.2f}" y="{anchor_y - 3:.2f}" '
                f'font-family="sans-serif" font-size="8" '
                f'fill="{_FOOTPRINT_STROKE}">{escape(placement.component)}</text>'
            )
            continue
        bx = min(xs)
        by = min(ys)
        w = max(xs) - bx
        h = max(ys) - by
        parts.append(
            f'<rect x="{x(bx):.2f}" y="{y(by + h):.2f}" '
            f'width="{w * px_per_mm:.2f}" height="{h * px_per_mm:.2f}" '
            f'fill="{_FOOTPRINT_FILL}" stroke="{_FOOTPRINT_STROKE}" '
            'stroke-width="0.8" opacity="0.6"/>'
        )
        parts.append(
            f'<text x="{x(bx):.2f}" y="{y(by + h) - 2:.2f}" '
            f'font-family="sans-serif" font-size="9" '
            f'fill="{_FOOTPRINT_STROKE}">{escape(placement.component)}</text>'
        )

    # Routes.
    for route in geom.routes.values():
        stroke = _ROUTE_STROKE.get(route.routing_class.value, "#2563eb")
        width_px = max(1.5, float(route.width) * px_per_mm)
        pts = " ".join(f"{x(float(p.x)):.2f},{y(float(p.y)):.2f}" for p in route.points)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" '
            f'stroke-width="{width_px:.2f}" stroke-linecap="butt" '
            'stroke-linejoin="miter" opacity="0.78"/>'
        )
        # Hollow circle markers at route endpoints.
        for endpoint in (route.points[0], route.points[-1]):
            parts.append(
                f'<circle cx="{x(float(endpoint.x)):.2f}" '
                f'cy="{y(float(endpoint.y)):.2f}" r="3" '
                f'fill="none" stroke="{stroke}" stroke-width="1.0" opacity="0.7"/>'
            )
        # Edge ID label at route midpoint.
        n = len(route.points)
        mid = route.points[n // 2]
        parts.append(
            f'<text x="{x(float(mid.x)) + 3:.2f}" y="{y(float(mid.y)) - 3:.2f}" '
            f'font-family="sans-serif" font-size="6" fill="{stroke}" opacity="0.85">'
            f"{escape(short_id(route.edge_id))}</text>"
        )

    # Junction / free node markers.
    for node_id, point in geom.nodes.items():
        cx = x(float(point.x))
        cy = y(float(point.y))
        parts.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.5" ' f'fill="{_NODE_FILL}"/>'
        )
        parts.append(
            f'<text x="{cx + 4:.2f}" y="{cy - 4:.2f}" '
            'font-family="sans-serif" font-size="7" fill="#1f2937">'
            f"{escape(short_id(node_id))}</text>"
        )

    # Status banner.
    parts.append(
        f'<text x="6" y="14" font-family="sans-serif" font-size="11" '
        f'fill="#0f172a">{escape(geom.project)} | status='
        f"{escape(geom.solve_status)} | wall="
        f"{geom.solve_wall_seconds:.3f}s</text>"
    )

    parts.append("</svg>")
    return "".join(parts)


__all__ = ["render_geometry_svg"]
