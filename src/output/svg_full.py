"""M6 final-layout SVG renderer.

Extends :func:`postproc.geom_svg.render_geometry_svg` with optional DRC
overlay (red dashed rectangles around violating segments), an optional
footprint-pad layer (M8), and a footer banner summarising bend / DRC / LVS
reports.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

from postproc.bend import BendReport
from postproc.drc import DrcReport
from postproc.geom_svg import render_geometry_svg
from postproc.lvs import LvsReport
from schema.geometry_ir import GeometryIR
from schema.v33 import V33Layout

_PAD_FILL = "#cbd5e1"
_PAD_STROKE = "#475569"


def _pad_polygon_world(
    cx: float,
    cy: float,
    rot_deg: float,
    local_x: float,
    local_y: float,
    local_orient_deg: float,
    pad_len: float,
    pad_w: float,
) -> list[tuple[float, float]]:
    """Return four world-space corner points (mm) of a rectangular pad.

    ``pad_len`` runs along the local orientation axis; ``pad_w`` is across.
    """

    theta = math.radians(rot_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    # World position of pad centre.
    wx = cx + local_x * cos_t - local_y * sin_t
    wy = cy + local_x * sin_t + local_y * cos_t
    # Combined rotation for the pad rectangle.
    phi = math.radians(rot_deg + local_orient_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    hl, hw = pad_len / 2.0, pad_w / 2.0
    locals_xy = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
    return [
        (wx + lx * cos_p - ly * sin_p, wy + lx * sin_p + ly * cos_p)
        for lx, ly in locals_xy
    ]


def _render_pads(
    layout: V33Layout,
    geom: GeometryIR,
    *,
    x_fn,  # type: ignore[no-untyped-def]
    y_fn,  # type: ignore[no-untyped-def]
) -> tuple[list[str], int]:
    """Render every pad of every placed component as an SVG polygon + pin label.

    Returns a tuple of (svg_elements, polygon_count). Pad **positions** come
    from :class:`GeometryIR.placements` (the post-solve resolved coordinates),
    while pad **dimensions** and the per-pin local orientation come from the
    original v3.3 layout's footprint library. Components present only in the
    layout (with no GeometryIR placement, e.g. data-only entries) are skipped.
    """

    parts: list[str] = []
    polygon_count = 0
    components = layout.components
    footprints = layout.footprints

    for comp_id, gp in geom.placements.items():
        comp = components.get(comp_id)
        if comp is None or comp.footprint_ref is None:
            continue
        fp = footprints.get(comp.footprint_ref)
        if fp is None:
            continue
        rot = float(gp.rotation_deg)
        for pad_placement in gp.pads:
            pin = fp.pins.get(pad_placement.pin)
            if pin is None:
                continue
            geom_pad = pin.pad_geometry
            if geom_pad is None or geom_pad.length is None or geom_pad.width is None:
                continue
            shape = (geom_pad.shape or "rect").lower()
            if shape != "rect":
                # M8 fallback: round / oblong pads still draw as rect bbox.
                pass
            wx = float(pad_placement.point.x)
            wy = float(pad_placement.point.y)
            local_orient = float(pin.local_orientation or 0.0)
            phi = math.radians(rot + local_orient)
            cos_p, sin_p = math.cos(phi), math.sin(phi)
            hl, hw = float(geom_pad.length) / 2.0, float(geom_pad.width) / 2.0
            corners_local = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
            corners = [
                (wx + lx * cos_p - ly * sin_p, wy + lx * sin_p + ly * cos_p)
                for lx, ly in corners_local
            ]
            pts_str = " ".join(f"{x_fn(px):.2f},{y_fn(py):.2f}" for px, py in corners)
            label = escape(f"{comp_id}.{pad_placement.pin}")
            parts.append(
                f'<polygon points="{pts_str}" fill="{_PAD_FILL}" '
                f'stroke="{_PAD_STROKE}" stroke-width="0.6" opacity="0.85">'
                f"<title>{label}</title></polygon>"
            )
            polygon_count += 1
            # Visible pin-number label centred on the pad.
            parts.append(
                f'<text x="{x_fn(wx):.2f}" y="{y_fn(wy) + 3:.2f}" '
                f'font-family="sans-serif" font-size="5.5" text-anchor="middle" '
                f'fill="{_PAD_STROKE}" font-weight="bold">'
                f"{escape(pad_placement.pin)}</text>"
            )
    return parts, polygon_count


def render_full_layout(
    geom: GeometryIR,
    *,
    bend: BendReport | None = None,
    drc: DrcReport | None = None,
    lvs: LvsReport | None = None,
    layout: V33Layout | None = None,
    show_pads: bool = True,
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    base = render_geometry_svg(geom, px_per_mm=px_per_mm, margin_mm=margin_mm)
    overlays: list[str] = []

    bw = float(geom.board.width) + 2 * margin_mm
    bh = float(geom.board.height) + 2 * margin_mm

    def _x(v: float) -> float:
        return (v + margin_mm) * px_per_mm

    def _y(v: float) -> float:
        return (bh - (v + margin_mm)) * px_per_mm

    pad_parts: list[str] = []
    pad_polygon_count = 0
    if layout is not None and show_pads:
        pad_parts, pad_polygon_count = _render_pads(layout, geom, x_fn=_x, y_fn=_y)

    if drc is not None and drc.violations:
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
    del bw  # only used inside helpers

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
    if pad_parts:
        footer_parts.append(f"pads={pad_polygon_count}")

    if pad_parts or overlays or footer_parts:
        # Inject pads BEFORE routes/overlays/footer so routes draw on top.
        # base SVG layers already contain board + footprint outlines + routes;
        # we splice pads into the existing sequence right before route group
        # by keying on the comment marker — but geom_svg has no marker, so we
        # insert pads at the start (after first <rect> background) and overlays
        # near </svg>.
        injection_top = "".join(pad_parts)
        injection_bottom = "".join(overlays)
        if footer_parts:
            footer_text = " | ".join(footer_parts)
            injection_bottom += (
                f'<text x="6" y="28" font-family="sans-serif" font-size="10" '
                f'fill="#111827">{escape(footer_text)}</text>'
            )
        if injection_top:
            # Insert just before the first route stroke. Routes start with
            # `<polyline ` in the base svg; fall back to before </svg> if no
            # route exists.
            marker = "<polyline "
            idx = base.find(marker)
            if idx != -1:
                base = base[:idx] + injection_top + base[idx:]
            else:
                base = base.replace("</svg>", injection_top + "</svg>")
        if injection_bottom:
            base = base.replace("</svg>", injection_bottom + "</svg>")

    return base


__all__ = ["render_full_layout"]
