"""M6 final-layout SVG renderer.

Extends :func:`postproc.geom_svg.render_geometry_svg` with optional DRC
overlay (red dashed rectangles around violating segments) and a footer
banner summarising bend / DRC / LVS reports.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from postproc.bend import BendReport
from postproc.drc import DrcReport
from postproc.geom_svg import render_geometry_svg
from postproc.lvs import LvsReport
from schema.geometry_ir import GeometryIR


def render_full_layout(
    geom: GeometryIR,
    *,
    bend: BendReport | None = None,
    drc: DrcReport | None = None,
    lvs: LvsReport | None = None,
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    base = render_geometry_svg(geom, px_per_mm=px_per_mm, margin_mm=margin_mm)
    overlays: list[str] = []

    if drc is not None and drc.violations:
        bw = float(geom.board.width) + 2 * margin_mm
        bh = float(geom.board.height) + 2 * margin_mm

        def _x(v: float) -> float:
            return (v + margin_mm) * px_per_mm

        def _y(v: float) -> float:
            return (bh - (v + margin_mm)) * px_per_mm

        for v in drc.violations:
            edges = [v.edge_a]
            if v.edge_b is not None:
                edges.append(v.edge_b)
            for eid in edges:
                route = geom.routes.get(eid)
                if route is None:
                    continue
                xs = [float(p.x) for p in route.points]
                ys = [float(p.y) for p in route.points]
                pad = float(route.width) / 2 + 0.3
                rx0 = _x(min(xs) - pad)
                rx1 = _x(max(xs) + pad)
                ry0 = _y(max(ys) + pad)
                ry1 = _y(min(ys) - pad)
                stroke = "#dc2626" if v.severity == "critical" else "#f59e0b"
                overlays.append(
                    f'<rect x="{rx0:.2f}" y="{ry0:.2f}" '
                    f'width="{rx1 - rx0:.2f}" height="{ry1 - ry0:.2f}" '
                    f'fill="none" stroke="{stroke}" stroke-width="1.4" '
                    'stroke-dasharray="4 3" opacity="0.95"/>'
                )
        del bw  # only used inside helpers above

    footer_parts: list[str] = []
    if bend is not None:
        footer_parts.append(
            f"bends={len(bend.bended_edges)}/skip={len(bend.skipped_edges)}"
            + (f"/warn={len(bend.warnings)}" if bend.warnings else "")
        )
    if drc is not None:
        footer_parts.append(f"DRC crit={drc.critical_count}/warn={drc.warning_count}")
    if lvs is not None:
        if lvs.skipped:
            footer_parts.append("LVS=skipped")
        else:
            footer_parts.append(f"LVS mismatch={len(lvs.mismatches)}")

    if overlays or footer_parts:
        injection = "".join(overlays)
        if footer_parts:
            footer_text = " | ".join(footer_parts)
            injection += (
                f'<text x="6" y="28" font-family="sans-serif" font-size="10" '
                f'fill="#111827">{escape(footer_text)}</text>'
            )
        # Insert overlays right before closing </svg>.
        base = base.replace("</svg>", injection + "</svg>")

    return base


__all__ = ["render_full_layout"]
