"""``pcb_solve_v2`` CLI — M10 skeleton-first three-phase pipeline.

Replacement for the v6 ``pcb_solve``: runs the new
``Frontend → Phase A skeleton routing → Phase B UV adhesion → Phase C
flex (no-op for PA) → Postproc`` flow and emits per-phase SVG/JSON
artefacts so each stage can be reviewed independently.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from output import render_full_layout
from frontend.solver_ir import compile_solver_ir
from schema.geometry_ir import GeometryIR
from schema.solver_ir import UniversalJunctionTemplate
from schema.v33 import V33Layout, load_v33_layout
from solver.v2 import OrchestratorV2Options, solve_layout_v2
from solver.v2.orchestrator import (
    _assemble_geometry,
    OrchestratorV2Result,
    phase_summary,
)
from solver.v2.uv_adhesion import UvAdhesionReport


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcb_solve_v2",
        description=(
            "M10 skeleton-first router: routes microstrip skeleton first, "
            "then snaps UV components to the routed endpoints."
        ),
    )
    p.add_argument("layout", type=Path, help="Path to v3.3 YAML layout.")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="Directory for per-phase artefacts (default: ./out).",
    )
    p.add_argument(
        "--clearance-mm",
        type=float,
        default=0.15,
        help="Default routing clearance in mm (default 0.15).",
    )
    p.add_argument(
        "--grid-step-um",
        type=int,
        default=200,
        help="A* grid step in µm (default 200).",
    )
    p.add_argument(
        "--rip-up-rounds",
        type=int,
        default=5,
        help="Maximum rip-up & reroute rounds (default 5).",
    )
    p.add_argument(
        "--svg-out",
        type=Path,
        default=None,
        help="Optional final SVG path (default: <out_dir>/<project>.final.svg).",
    )
    p.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Optional summary JSON path (default: <out_dir>/<project>.summary.json).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Skip per-edge progress printing.",
    )
    return p


def _build_phase_a_geom(result: OrchestratorV2Result) -> GeometryIR:
    """Geometry snapshot after Phase A: skeleton routes only, no UV placements."""
    return _assemble_geometry(
        artifact=result.artifact,
        plan=result.phase_a.plan,
        skeleton=result.phase_a.skeleton,
        adhesion=UvAdhesionReport(),  # empty — no UV yet
        wall_seconds=result.phase_a.wall_seconds,
    )


def _build_phase_b_geom(result: OrchestratorV2Result) -> GeometryIR:
    """Geometry snapshot after Phase B: skeleton routes + UV placements."""
    return _assemble_geometry(
        artifact=result.artifact,
        plan=result.phase_a.plan,
        skeleton=result.phase_a.skeleton,
        adhesion=result.phase_b.adhesion,
        wall_seconds=result.phase_a.wall_seconds + result.phase_b.wall_seconds,
    )


def _phase_banner(phase: str, description: str, color: str) -> str:
    """Return an SVG <text> element showing the phase label near the top."""
    return (
        f'<text x="6" y="26" font-family="sans-serif" font-size="9" '
        f'font-weight="bold" fill="{color}">'
        f"[{escape(phase)}] {escape(description)}</text>"
    )


def _uv_highlight_overlay(
    geom: GeometryIR, uv_names: set[str], px_per_mm: float = 6.0, margin_mm: float = 5.0
) -> str:
    """SVG overlay that redraws UV-placed components in green so they stand out."""
    board_h = float(geom.board.height) + 2 * margin_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h - (mm + margin_mm)) * px_per_mm

    parts: list[str] = []
    for name, placement in geom.placements.items():
        if name not in uv_names or not placement.pads:
            continue
        xs = [float(p.point.x) for p in placement.pads]
        ys = [float(p.point.y) for p in placement.pads]
        if max(xs) == min(xs) or max(ys) == min(ys):
            for pad in placement.pads:
                parts.append(
                    f'<circle cx="{_x(float(pad.point.x)):.2f}" '
                    f'cy="{_y(float(pad.point.y)):.2f}" r="3" '
                    'fill="#16a34a" opacity="0.9"/>'
                )
            parts.append(
                f'<text x="{_x(float(xs[0])) + 4:.2f}" '
                f'y="{_y(float(ys[0])) - 3:.2f}" '
                'font-family="sans-serif" font-size="8" fill="#15803d" font-weight="bold">'
                f"{escape(name)}</text>"
            )
            continue
        bx, by = min(xs), min(ys)
        w = max(xs) - bx
        h = max(ys) - by
        parts.append(
            f'<rect x="{_x(bx):.2f}" y="{_y(by + h):.2f}" '
            f'width="{w * px_per_mm:.2f}" height="{h * px_per_mm:.2f}" '
            'fill="#bbf7d0" stroke="#16a34a" stroke-width="1.5" opacity="0.9"/>'
        )
        parts.append(
            f'<text x="{_x(bx):.2f}" y="{_y(by + h) - 2:.2f}" '
            'font-family="sans-serif" font-size="8" fill="#15803d" font-weight="bold">'
            f"{escape(name)}</text>"
        )
    return "".join(parts)


def _pin_label_text(pin: str) -> str:
    if pin.startswith("P") and pin[1:].isdigit():
        return f"PIN_{pin[1:]}"
    if pin.startswith("PIN_"):
        return pin
    return pin


def _pin_label_overlay(
    geom: GeometryIR, px_per_mm: float = 6.0, margin_mm: float = 5.0
) -> str:
    """SVG overlay to show per-pad pin numbers from GeometryIR placements."""
    board_h = float(geom.board.height) + 2 * margin_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h - (mm + margin_mm)) * px_per_mm

    parts: list[str] = []
    for placement in geom.placements.values():
        for pad in placement.pads:
            px = float(pad.point.x)
            py = float(pad.point.y)
            label = escape(_pin_label_text(str(pad.pin)))
            parts.append(
                f'<text x="{_x(px):.2f}" y="{_y(py) - 2.0:.2f}" '
                'font-family="sans-serif" font-size="6" text-anchor="middle" '
                'font-weight="bold" fill="#111827" stroke="#ffffff" stroke-width="0.8" paint-order="stroke">'
                f"{label}</text>"
            )
    return "".join(parts)


def _render_svg(
    geom: GeometryIR,
    path: Path,
    *,
    banner: str = "",
    overlay: str = "",
    layout: V33Layout | None = None,
) -> None:
    """Write a rendered SVG to *path*, injecting an optional phase banner and overlay."""
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = render_full_layout(geom, layout=layout)
    if overlay:
        svg = svg.replace("</svg>", overlay + "</svg>")
    if banner:
        svg = svg.replace("</svg>", banner + "</svg>")
    path.write_text(svg)


def _render_pre_phase_svg(
    result: OrchestratorV2Result,
    path: Path,
    *,
    templates: dict[str, UniversalJunctionTemplate] | None = None,
    branch_offset_u_tokens: dict[str, str] | None = None,
    layout: V33Layout | None = None,
    banner: str = "",
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> tuple[
    dict[str, tuple[float, float]],
    set[str],
    dict[str, dict[str, tuple[float, float]]],
]:
    """Render physical Pre-A view in board coordinates.

    Shows board outline + fixed points + microstrip connectivity with width/length labels.
    UV/RLC devices are rendered from footprint pads + component bboxes when layout is available.
    Returns endpoint positions (mm) and the virtual endpoint id set for JSON sidecar output.
    """
    artifact = result.artifact
    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))
    positions, edge_endpoint_overrides = _solve_pre_a_positions(
        result,
        board_w=board_w,
        board_h=board_h,
        junction_templates=templates,
        branch_offset_u_tokens=branch_offset_u_tokens,
    )
    virtual_endpoints = {
        endpoint
        for endpoint in positions
        if _prea_endpoint_kind(artifact, endpoint) == "virtual_rlc_pin"
    }

    svg_w = (board_w + 2 * margin_mm) * px_per_mm
    svg_h = (board_h + 2 * margin_mm) * px_per_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h + 2 * margin_mm - (mm + margin_mm)) * px_per_mm

    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.1f}" height="{svg_h:.1f}" '
        f'viewBox="0 0 {svg_w:.1f} {svg_h:.1f}" role="img" aria-label="Pre-A connectivity">',
        "<style>",
        ".prea-board{fill:none;stroke:#111827;stroke-width:1.8;}",
        ".prea-fixed-point{fill:#1d4ed8;stroke:#1e3a8a;stroke-width:1;}",
        ".prea-node{fill:#22c55e;stroke:#166534;stroke-width:1;}",
        ".prea-virtual-endpoint{fill:#f59e0b;stroke:#92400e;stroke-width:1;}",
        ".prea-rlc-pad{fill:#cbd5e1;stroke:#475569;stroke-width:0.7;opacity:0.9;}",
        ".prea-rlc-bbox{fill:none;stroke:#0f766e;stroke-width:0.8;stroke-dasharray:2 2;opacity:0.9;}",
        ".prea-assist-link{stroke:#475569;stroke-width:0.7;stroke-dasharray:2 2;opacity:0.95;}",
        ".prea-edge-bridge{stroke:#64748b;stroke-width:0.9;stroke-dasharray:2 2;opacity:0.9;}",
        ".prea-edge{fill:none;stroke-linecap:butt;stroke-linejoin:miter;opacity:0.95;}",
        ".prea-label{fill:#0f172a;font-family:Arial,sans-serif;font-size:8px;}",
        ".prea-net-label{fill:#7c2d12;font-family:Arial,sans-serif;font-size:7px;font-weight:bold;}",
        ".prea-gnd-pin{fill:#f59e0b;stroke:#92400e;stroke-width:0.8;}",
        ".prea-fixed-bbox{fill:none;stroke:#1f2937;stroke-width:0.9;stroke-dasharray:2 1;opacity:0.9;}",
        ".prea-fixed-pad{fill:#dbeafe;stroke:#1e40af;stroke-width:0.8;opacity:0.95;}",
        "</style>",
        f'<rect class="prea-board" x="{_x(0):.2f}" y="{_y(board_h):.2f}" width="{board_w * px_per_mm:.2f}" height="{board_h * px_per_mm:.2f}"/>',
    ]

    endpoint_vectors: dict[str, list[tuple[float, float, float]]] = {}
    constrained_draw_segments: list[
        tuple[str, tuple[float, float], tuple[float, float]]
    ] = []

    def _add_endpoint_vector(
        endpoint_id: str, vx: float, vy: float, weight: float
    ) -> None:
        norm = math.hypot(vx, vy)
        if norm < 1e-9:
            return
        ux, uy = vx / norm, vy / norm
        for token in _expand_endpoint_tokens(endpoint_id):
            endpoint_vectors.setdefault(token, []).append((ux, uy, weight))
        endpoint_vectors.setdefault(endpoint_id, []).append((ux, uy, weight))

    # Draw microstrip edges with width/length labels.
    for edge_id, edge in sorted(artifact.edges.items()):
        if edge.edge_type != "microstrip" or len(edge.connections) != 2:
            continue
        start_id, end_id = edge.connections
        if start_id not in positions or end_id not in positions:
            continue
        overrides = edge_endpoint_overrides.get(edge_id, {})
        sx, sy = overrides.get(start_id, positions[start_id])
        ex, ey = overrides.get(end_id, positions[end_id])
        main_sx, main_sy = sx, sy
        main_ex, main_ey = ex, ey
        bridge: tuple[float, float, float, float] | None = None
        if edge.target_length is not None:
            desired = float(edge.target_length)
            seg_len = math.hypot(ex - sx, ey - sy)
            start_kind = _prea_endpoint_kind(artifact, start_id)
            end_kind = _prea_endpoint_kind(artifact, end_id)
            if abs(seg_len - desired) > 0.25:
                pin_oriented_anchor: str | None = None
                for candidate in (start_id, end_id):
                    pad = artifact.fixed_terminals.get(candidate)
                    if (
                        pad is not None
                        and pad.orientation is not None
                        and float(pad.orientation) != 0.0
                    ):
                        pin_oriented_anchor = candidate
                        break

                if pin_oriented_anchor is not None:
                    ax, ay = positions[pin_oriented_anchor]
                    anchor_pad = artifact.fixed_terminals[pin_oriented_anchor]
                    orientation = (
                        float(anchor_pad.orientation)
                        if anchor_pad.orientation is not None
                        else 0.0
                    )
                    theta = math.radians(orientation)
                    px = ax + math.cos(theta) * desired
                    py = ay + math.sin(theta) * desired
                    if pin_oriented_anchor == start_id:
                        bx, by = ex, ey
                        main_sx, main_sy, main_ex, main_ey = ax, ay, px, py
                    else:
                        bx, by = sx, sy
                        main_sx, main_sy, main_ex, main_ey = px, py, ax, ay
                    bridge = (px, py, bx, by)
                elif start_kind == "fixed_pin" and end_kind != "fixed_pin":
                    ax, ay = ex, ey
                    bx, by = sx, sy
                    anchor_is_start = False
                elif end_kind == "fixed_pin" and start_kind != "fixed_pin":
                    ax, ay = sx, sy
                    bx, by = ex, ey
                    anchor_is_start = True
                else:
                    ax, ay = sx, sy
                    bx, by = ex, ey
                    anchor_is_start = True
                if pin_oriented_anchor is None:
                    ux = (bx - ax) / seg_len
                    uy = (by - ay) / seg_len
                    px = ax + ux * desired
                    py = ay + uy * desired
                    if anchor_is_start:
                        main_sx, main_sy, main_ex, main_ey = ax, ay, px, py
                    else:
                        main_sx, main_sy, main_ex, main_ey = px, py, ax, ay
                    bridge = (px, py, bx, by)
        stroke_width = max(float(edge.width or 0.2) * px_per_mm, 1.2)
        length_text = (
            f"L={float(edge.target_length):.1f}mm"
            if edge.target_length is not None
            else "L=n/a"
        )
        width_text = f"w={float(edge.width or 0.0):.1f}mm"
        lines.append(
            f'<line class="prea-edge" data-edge-id="{escape(edge_id)}" '
            f'x1="{_x(main_sx):.2f}" y1="{_y(main_sy):.2f}" '
            f'x2="{_x(main_ex):.2f}" y2="{_y(main_ey):.2f}" '
            f'stroke="#334155" stroke-width="{stroke_width:.2f}"><title>'
            f"{escape(edge_id)} | {escape(width_text)} | {escape(length_text)}</title></line>"
        )
        if bridge is not None:
            bx1, by1, bx2, by2 = bridge
            lines.append(
                f'<line class="prea-edge-bridge" data-edge-id="{escape(edge_id)}" '
                f'x1="{_x(bx1):.2f}" y1="{_y(by1):.2f}" x2="{_x(bx2):.2f}" y2="{_y(by2):.2f}"/>'
            )
        weight = float(edge.target_length) if edge.target_length is not None else 0.0
        _add_endpoint_vector(start_id, main_ex - main_sx, main_ey - main_sy, weight)
        _add_endpoint_vector(end_id, main_sx - main_ex, main_sy - main_ey, weight)
        mx = (main_sx + main_ex) / 2.0
        my = (main_sy + main_ey) / 2.0
        short_edge_id = _prea_short_name(edge_id)
        if edge.target_length is not None:
            constrained_draw_segments.append(
                (edge_id, (main_sx, main_sy), (main_ex, main_ey))
            )
            lines.append(
                f'<text class="prea-label" x="{_x(mx)+4:.2f}" y="{_y(my)-4:.2f}">'
                f"{escape(short_edge_id)}</text>"
            )

    pad_centers: dict[str, tuple[float, float]] = {}
    if layout is not None:
        uv_display_shift: dict[str, tuple[float, float]] = {}
        overlap_groups: dict[
            tuple[float, float, float, float],
            list[tuple[str, tuple[float, float], tuple[float, float]]],
        ] = {}
        endpoint_edge_ids: dict[str, set[str]] = {}
        for edge_name, edge in artifact.edges.items():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            for endpoint in edge.connections:
                for token in _expand_endpoint_tokens(endpoint):
                    endpoint_edge_ids.setdefault(token, set()).add(edge_name)

        def _point_to_segment_distance(
            px: float, py: float, a: tuple[float, float], b: tuple[float, float]
        ) -> float:
            ax, ay = a
            bx, by = b
            vx = bx - ax
            vy = by - ay
            seg2 = vx * vx + vy * vy
            if seg2 <= 1e-12:
                return math.hypot(px - ax, py - ay)
            t = ((px - ax) * vx + (py - ay) * vy) / seg2
            t = max(0.0, min(1.0, t))
            cx = ax + t * vx
            cy = ay + t * vy
            return math.hypot(px - cx, py - cy)

        for comp_id, uv in artifact.uv_components.items():
            component = layout.components.get(comp_id)
            if component is None or component.footprint_ref is None:
                continue
            footprint = layout.footprints.get(component.footprint_ref)
            if footprint is None or len(footprint.pins) != 2:
                continue
            pin_world_exact: list[tuple[float, float]] = []
            for pin_name in footprint.pins:
                endpoint = f"{comp_id}.{pin_name}"
                xy = positions.get(endpoint)
                if xy is None:
                    pin_world_exact = []
                    break
                pin_world_exact.append(xy)
            if len(pin_world_exact) != 2:
                continue
            (ax, ay), (bx, by) = pin_world_exact
            if (ax, ay) > (bx, by):
                ax, ay, bx, by = bx, by, ax, ay
            key = (round(ax, 3), round(ay, 3), round(bx, 3), round(by, 3))
            overlap_groups.setdefault(key, []).append(
                (comp_id, pin_world_exact[0], pin_world_exact[1])
            )
        for grouped in overlap_groups.values():
            if len(grouped) <= 1:
                continue
            grouped.sort(key=lambda item: item[0])
            _, a_xy, b_xy = grouped[0]
            dx = b_xy[0] - a_xy[0]
            dy = b_xy[1] - a_xy[1]
            norm = math.hypot(dx, dy)
            if norm <= 1e-6:
                nx, ny = 0.0, 1.0
            else:
                nx, ny = -dy / norm, dx / norm
            spacing = 0.55
            center_idx = (len(grouped) - 1) / 2.0
            for idx, (comp_id, _, _) in enumerate(grouped):
                delta = (idx - center_idx) * spacing
                uv_display_shift[comp_id] = (nx * delta, ny * delta)

        rendered_uv_components: set[str] = set()
        for comp_id, uv in artifact.uv_components.items():
            component = layout.components.get(comp_id)
            if component is None or component.footprint_ref is None:
                continue
            footprint = layout.footprints.get(component.footprint_ref)
            if footprint is None or not footprint.pins:
                continue
            gnd_pins = [
                pin_name
                for pin_name, net_name in component.pin_nets.items()
                if net_name.strip().upper() == "GND"
            ]
            pin_world: dict[str, tuple[float, float]] = {}
            for pin_name in footprint.pins:
                endpoint = f"{comp_id}.{pin_name}"
                if endpoint in positions:
                    pin_world[pin_name] = positions[endpoint]
            if not pin_world:
                continue
            shift_x, shift_y = uv_display_shift.get(comp_id, (0.0, 0.0))
            pin_world_draw = {
                pin_name: (xy[0] + shift_x, xy[1] + shift_y)
                for pin_name, xy in pin_world.items()
            }
            if len(footprint.pins) == 2:
                pin_names = list(footprint.pins.keys())
                ep_a = f"{comp_id}.{pin_names[0]}"
                ep_b = f"{comp_id}.{pin_names[1]}"
                cnt_a = len(endpoint_edge_ids.get(ep_a, set()))
                cnt_b = len(endpoint_edge_ids.get(ep_b, set()))
                if (cnt_a > 0 and cnt_b == 0) or (cnt_b > 0 and cnt_a == 0):
                    known_pin, missing_pin = (
                        (pin_names[0], pin_names[1])
                        if cnt_a > 0
                        else (pin_names[1], pin_names[0])
                    )
                    known_ep = f"{comp_id}.{known_pin}"
                    known_xy = pin_world_draw.get(known_pin)
                    if known_xy is not None:
                        p_known = footprint.pins.get(known_pin)
                        p_missing = footprint.pins.get(missing_pin)
                        if (
                            p_known is not None
                            and p_missing is not None
                            and p_known.local_x is not None
                            and p_known.local_y is not None
                            and p_missing.local_x is not None
                            and p_missing.local_y is not None
                        ):
                            pitch = math.hypot(
                                float(p_missing.local_x) - float(p_known.local_x),
                                float(p_missing.local_y) - float(p_known.local_y),
                            )
                            ref_u: tuple[float, float] | None = None
                            for edge_name in endpoint_edge_ids.get(known_ep, set()):
                                edge_obj = artifact.edges.get(edge_name)
                                if (
                                    edge_obj is None
                                    or edge_obj.edge_type != "microstrip"
                                    or len(edge_obj.connections) != 2
                                ):
                                    continue
                                a_id, b_id = edge_obj.connections
                                a_tokens = _expand_endpoint_tokens(a_id)
                                b_tokens = _expand_endpoint_tokens(b_id)
                                if known_ep in a_tokens:
                                    trace_endpoint = b_id
                                elif known_ep in b_tokens:
                                    trace_endpoint = a_id
                                else:
                                    continue
                                if (
                                    _prea_endpoint_kind(artifact, trace_endpoint)
                                    == "virtual_rlc_pin"
                                ):
                                    continue
                                for edge2 in artifact.edges.values():
                                    if (
                                        edge2.edge_type != "microstrip"
                                        or len(edge2.connections) != 2
                                        or edge2.target_length is None
                                        or float(edge2.target_length) <= 0.0
                                    ):
                                        continue
                                    c_id, d_id = edge2.connections
                                    if c_id == trace_endpoint:
                                        other2 = d_id
                                    elif d_id == trace_endpoint:
                                        other2 = c_id
                                    else:
                                        continue
                                    if other2 not in positions:
                                        continue
                                    ox, oy = positions[other2]
                                    tx, ty = positions[trace_endpoint]
                                    vx = tx - ox
                                    vy = ty - oy
                                    norm = math.hypot(vx, vy)
                                    if norm > 1e-6:
                                        ref_u = (vx / norm, vy / norm)
                                        break
                                if ref_u is not None:
                                    break
                            if ref_u is None:
                                ref_u = (1.0, 0.0)
                            elif abs(ref_u[0]) >= abs(ref_u[1]):
                                ref_u = (1.0 if ref_u[0] >= 0.0 else -1.0, 0.0)
                            else:
                                ref_u = (0.0, 1.0 if ref_u[1] >= 0.0 else -1.0)
                            ux, uy = ref_u
                            candidates = [(-uy, ux), (ux, uy), (uy, -ux)]

                            def _score(vec: tuple[float, float]) -> tuple[float, float]:
                                tx = known_xy[0] + vec[0] * pitch
                                ty = known_xy[1] + vec[1] * pitch
                                vals: list[float] = []
                                known_edges = endpoint_edge_ids.get(known_ep, set())
                                for edge_name, s_xy, e_xy in constrained_draw_segments:
                                    if edge_name in known_edges:
                                        continue
                                    vals.append(
                                        _point_to_segment_distance(tx, ty, s_xy, e_xy)
                                    )
                                return (
                                    min(vals) if vals else 1e6,
                                    abs(vec[0]) + abs(vec[1]),
                                )

                            best = max(candidates, key=_score)
                            pin_world_draw[missing_pin] = (
                                known_xy[0] + best[0] * pitch,
                                known_xy[1] + best[1] * pitch,
                            )
            rotation = 0.0
            pin_items = sorted(pin_world_draw.items())
            if len(pin_items) >= 2:
                (_, a_xy), (_, b_xy) = pin_items[0], pin_items[1]
                dx = b_xy[0] - a_xy[0]
                dy = b_xy[1] - a_xy[1]
                if math.hypot(dx, dy) > 1e-6:
                    rotation = math.degrees(math.atan2(dy, dx))
            ct = math.cos(math.radians(rotation))
            st = math.sin(math.radians(rotation))
            if (
                footprint.dimensions is not None
                and footprint.dimensions.length is not None
                and footprint.dimensions.width is not None
            ):
                half_w = float(footprint.dimensions.width) / 2.0
                hw = float(footprint.dimensions.width) / 2.0
                if len(pin_items) >= 2:
                    u = (ct, st)
                    n = (-st, ct)
                    proj_u = [xy[0] * u[0] + xy[1] * u[1] for _, xy in pin_items]
                    proj_n = [xy[0] * n[0] + xy[1] * n[1] for _, xy in pin_items]
                    pad_extra = 0.0
                    for pin_name, pin in footprint.pins.items():
                        geom_pad = pin.pad_geometry
                        if (
                            geom_pad is not None
                            and geom_pad.length is not None
                            and pin_name in pin_world_draw
                        ):
                            pad_extra = max(pad_extra, float(geom_pad.length) / 2.0)
                    min_u = min(proj_u) - pad_extra
                    max_u = max(proj_u) + pad_extra
                    mid_n = sum(proj_n) / len(proj_n)
                    corners_un = [
                        (min_u, mid_n - half_w),
                        (max_u, mid_n - half_w),
                        (max_u, mid_n + half_w),
                        (min_u, mid_n + half_w),
                    ]
                    corners: list[tuple[float, float]] = []
                    for pu, pn in corners_un:
                        px = pu * u[0] + pn * n[0]
                        py = pu * u[1] + pn * n[1]
                        corners.append((px, py))
                else:
                    _, center_xy = pin_items[0]
                    hl = float(footprint.dimensions.length) / 2.0
                    corners = [
                        (center_xy[0] - hl, center_xy[1] - hw),
                        (center_xy[0] + hl, center_xy[1] - hw),
                        (center_xy[0] + hl, center_xy[1] + hw),
                        (center_xy[0] - hl, center_xy[1] + hw),
                    ]
                pts: list[str] = []
                for px, py in corners:
                    pts.append(f"{_x(px):.2f},{_y(py):.2f}")
                lines.append(
                    f'<path class="prea-rlc-bbox" d="M {" L ".join(pts)} Z"><title>{escape(comp_id)}</title></path>'
                )
                if gnd_pins:
                    gnd_anchor = next(
                        (
                            pin_world_draw[pin_name]
                            for pin_name in gnd_pins
                            if pin_name in pin_world_draw
                        ),
                        None,
                    )
                    if gnd_anchor is None:
                        cx = sum(px for px, _ in corners) / len(corners)
                        cy = sum(py for _, py in corners) / len(corners)
                        gnd_anchor = (cx, cy)
                    gx, gy = gnd_anchor
                    lines.append(
                        f'<text class="prea-net-label" x="{_x(gx)+4:.2f}" y="{_y(gy)+8:.2f}">GND</text>'
                    )
                    for idx, pin_name in enumerate(gnd_pins):
                        pin_xy = pin_world_draw.get(pin_name)
                        if pin_xy is None:
                            pin_xy = (gx + idx * 0.15, gy - idx * 0.15)
                        px, py = pin_xy
                        lines.append(
                            f'<rect class="prea-gnd-pin" x="{_x(px)-2.2:.2f}" y="{_y(py)-2.2:.2f}" width="4.4" height="4.4"/>'
                        )
            for pin_name, pin in footprint.pins.items():
                geom_pad = pin.pad_geometry
                if (
                    geom_pad is None
                    or geom_pad.length is None
                    or geom_pad.width is None
                    or (geom_pad.shape or "rect").lower() != "rect"
                    or pin_name not in pin_world_draw
                ):
                    continue
                endpoint = f"{comp_id}.{pin_name}"
                if endpoint in pin_world:
                    pad_centers[endpoint] = pin_world[pin_name]
                pcx, pcy = pin_world_draw[pin_name]
                orient = rotation + float(pin.local_orientation or 0.0)
                cp = math.cos(math.radians(orient))
                sp = math.sin(math.radians(orient))
                hl = float(geom_pad.length) / 2.0
                hw = float(geom_pad.width) / 2.0
                corners = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
                pad_pts: list[str] = []
                for lx, ly in corners:
                    px = pcx + cp * lx - sp * ly
                    py = pcy + sp * lx + cp * ly
                    pad_pts.append(f"{_x(px):.2f},{_y(py):.2f}")
                lines.append(
                    f'<polygon class="prea-rlc-pad" points="{" ".join(pad_pts)}"><title>{escape(comp_id)}.{escape(pin_name)}</title></polygon>'
                )
            rendered_uv_components.add(comp_id)

        # Render fixed components (e.g. IC1) with package outline and pads.
        for comp_id, component in layout.components.items():
            if comp_id in rendered_uv_components or component.footprint_ref is None:
                continue
            placement = component.placement
            if placement is None or placement.x is None or placement.y is None:
                continue
            footprint = layout.footprints.get(component.footprint_ref)
            if footprint is None:
                continue
            ox, oy = float(placement.x), float(placement.y)
            rotation = float(placement.rotation or 0.0)
            ct = math.cos(math.radians(rotation))
            st = math.sin(math.radians(rotation))

            if (
                footprint.dimensions is not None
                and footprint.dimensions.length is not None
                and footprint.dimensions.width is not None
            ):
                hl = float(footprint.dimensions.length) / 2.0
                hw = float(footprint.dimensions.width) / 2.0
                local_corners = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
                bbox_pts: list[str] = []
                for lx, ly in local_corners:
                    px = ox + ct * lx - st * ly
                    py = oy + st * lx + ct * ly
                    bbox_pts.append(f"{_x(px):.2f},{_y(py):.2f}")
                lines.append(
                    f'<path class="prea-fixed-bbox" d="M {" L ".join(bbox_pts)} Z"><title>{escape(comp_id)}</title></path>'
                )

            for pin_name, pin in footprint.pins.items():
                if pin.local_x is None or pin.local_y is None:
                    continue
                plx = float(pin.local_x)
                ply = float(pin.local_y)
                pcx = ox + ct * plx - st * ply
                pcy = oy + st * plx + ct * ply
                geom_pad = pin.pad_geometry
                if (
                    geom_pad is None
                    or geom_pad.length is None
                    or geom_pad.width is None
                    or (geom_pad.shape or "rect").lower() != "rect"
                ):
                    lines.append(
                        f'<rect class="prea-fixed-pad" x="{_x(pcx)-2.0:.2f}" y="{_y(pcy)-2.0:.2f}" width="4.0" height="4.0"><title>{escape(comp_id)}.{escape(pin_name)}</title></rect>'
                    )
                    continue
                orient = rotation + float(pin.local_orientation or 0.0)
                cp = math.cos(math.radians(orient))
                sp = math.sin(math.radians(orient))
                hl = float(geom_pad.length) / 2.0
                hw = float(geom_pad.width) / 2.0
                fixed_pad_pts: list[str] = []
                for lx, ly in [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]:
                    px = pcx + cp * lx - sp * ly
                    py = pcy + sp * lx + cp * ly
                    fixed_pad_pts.append(f"{_x(px):.2f},{_y(py):.2f}")
                lines.append(
                    f'<polygon class="prea-fixed-pad" points="{" ".join(fixed_pad_pts)}"><title>{escape(comp_id)}.{escape(pin_name)}</title></polygon>'
                )

    for endpoint, (pad_x, pad_y) in sorted(pad_centers.items()):
        target = positions.get(endpoint)
        if target is None:
            continue
        tx, ty = target
        if math.hypot(tx - pad_x, ty - pad_y) <= 0.2:
            continue
        lines.append(
            f'<line class="prea-assist-link" data-endpoint="{escape(endpoint)}" '
            f'x1="{_x(pad_x):.2f}" y1="{_y(pad_y):.2f}" x2="{_x(tx):.2f}" y2="{_y(ty):.2f}"/>'
        )

    # Draw fixed points / node points / virtual endpoints.
    for endpoint, (px, py) in sorted(positions.items()):
        kind = _prea_endpoint_kind(artifact, endpoint)
        if kind == "fixed_pin":
            lines.append(
                f'<circle class="prea-fixed-point" cx="{_x(px):.2f}" cy="{_y(py):.2f}" r="3.2"/>'
            )
            lines.append(
                f'<text class="prea-label" x="{_x(px)+4:.2f}" y="{_y(py)-4:.2f}">{escape(_prea_short_name(endpoint))}</text>'
            )
        elif kind == "node":
            lines.append(
                f'<circle class="prea-node" cx="{_x(px):.2f}" cy="{_y(py):.2f}" r="2.8"/>'
            )
        elif kind == "virtual_rlc_pin":
            cx = _x(px)
            cy = _y(py)
            points = f"{cx:.2f},{cy-3.8:.2f} {cx+3.8:.2f},{cy:.2f} {cx:.2f},{cy+3.8:.2f} {cx-3.8:.2f},{cy:.2f}"
            lines.append(f'<polygon class="prea-virtual-endpoint" points="{points}"/>')
            lines.append(
                f'<text class="prea-label" x="{cx+4:.2f}" y="{cy-4:.2f}">{escape(_prea_short_name(endpoint))}</text>'
            )

    if banner:
        lines.append(banner)
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return positions, virtual_endpoints, edge_endpoint_overrides


def _load_junction_templates(
    *, layout_path: Path
) -> dict[str, UniversalJunctionTemplate]:
    try:
        ir = compile_solver_ir(layout_path)
    except Exception:
        return {}
    return dict(ir.junction_templates)


def _load_branch_offset_u_tokens(*, layout_path: Path) -> dict[str, str]:
    """Return symbolic branch origin.offset_u tokens keyed by branch edge id."""
    try:
        layout = load_v33_layout(layout_path)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for node in layout.nodes.values():
        rules = node.connection_rules
        if node.type != "universal_junction" or rules is None or not rules.branches:
            continue
        for branch in rules.branches:
            if branch.edge is None or branch.origin is None:
                continue
            raw = branch.origin.offset_u
            if isinstance(raw, str):
                out[branch.edge] = raw.strip().lower()
    return out


def _prea_endpoint_kind(artifact, endpoint_id: str) -> str:  # type: ignore[no-untyped-def]
    if endpoint_id in artifact.fixed_terminals:
        return "fixed_pin"
    if endpoint_id in artifact.nodes:
        return "node"
    if "." in endpoint_id:
        comp = endpoint_id.split(".", 1)[0]
        if comp in artifact.uv_components:
            return "virtual_rlc_pin"
    return "other"


def _expand_endpoint_tokens(endpoint_id: str) -> tuple[str, ...]:
    if "," not in endpoint_id:
        return (endpoint_id,)
    return tuple(part.strip() for part in endpoint_id.split(",") if part.strip())


def _prea_short_name(name: str) -> str:
    out = name
    out = out.replace("IC1_pin1_", "p1_")
    out = out.replace("IC1_pin2_", "p2_")
    out = out.replace("_universal_node", "_u")
    out = out.replace("_end_split_pad", "_sp")
    out = out.replace("_start_combiner", "_sc")
    out = out.replace("_to_", "->")
    out = out.replace(".PIN_", ".")
    return out


def _clamp_board(
    p: tuple[float, float], board_w: float, board_h: float
) -> tuple[float, float]:
    """Clamp point p to [0, board_w] × [0, board_h]."""
    return (min(max(p[0], 0.0), board_w), min(max(p[1], 0.0), board_h))


def _seed_fixed_positions(
    artifact: Any,
    plan_xy: dict[str, tuple[float, float]],
    endpoints: set[str],
    board_w: float,
    board_h: float,
) -> tuple[
    dict[str, tuple[float, float]],
    set[str],
    set[str],
]:
    """Seed position dict from fixed terminals (abs_x/abs_y) and plan_xy.

    Returns (positions, fixed, constrained_endpoints).
    """
    positions: dict[str, tuple[float, float]] = {}
    fixed: set[str] = set()
    for endpoint in endpoints:
        fixed_pad = artifact.fixed_terminals.get(endpoint)
        if (
            fixed_pad is not None
            and fixed_pad.abs_x is not None
            and fixed_pad.abs_y is not None
        ):
            positions[endpoint] = (float(fixed_pad.abs_x), float(fixed_pad.abs_y))
            fixed.add(endpoint)
            continue
        seed = plan_xy.get(endpoint)
        if seed is not None:
            positions[endpoint] = (float(seed[0]), float(seed[1]))
        else:
            positions[endpoint] = (board_w / 2.0, board_h / 2.0)

    constrained_endpoints: set[str] = set()
    launch_targets: dict[str, list[tuple[float, float]]] = {}
    for edge in artifact.edges.values():
        if edge.edge_type != "microstrip" or len(edge.connections) != 2:
            continue
        a_id, b_id = edge.connections
        for src_id, dst_id in ((a_id, b_id), (b_id, a_id)):
            if src_id not in fixed or dst_id in fixed:
                continue
            src_pad = artifact.fixed_terminals.get(src_id)
            if src_pad is None or src_pad.orientation is None:
                continue
            sx, sy = positions[src_id]
            dx = positions[dst_id][0] - sx
            dy = positions[dst_id][1] - sy
            seed_len = math.hypot(dx, dy)
            seg_len = (
                float(edge.target_length)
                if edge.target_length is not None
                else max(seed_len, 1.0)
            )
            theta = math.radians(float(src_pad.orientation))
            tx = sx + math.cos(theta) * seg_len
            ty = sy + math.sin(theta) * seg_len
            launch_targets.setdefault(dst_id, []).append(
                _clamp_board((tx, ty), board_w, board_h)
            )
    for endpoint, candidates in launch_targets.items():
        if endpoint in fixed or not candidates:
            continue
        avg_x = sum(x for x, _ in candidates) / len(candidates)
        avg_y = sum(y for _, y in candidates) / len(candidates)
        positions[endpoint] = _clamp_board((avg_x, avg_y), board_w, board_h)
        constrained_endpoints.add(endpoint)

    return positions, fixed, constrained_endpoints


def _apply_junction_templates(
    artifact: Any,
    positions: dict[str, tuple[float, float]],
    fixed: set[str],
    constrained_endpoints: set[str],
    board_w: float,
    board_h: float,
    templates: dict[str, UniversalJunctionTemplate],
    branch_offset_u_tokens: dict[str, str] | None,
) -> tuple[set[str], dict[str, dict[str, tuple[float, float]]]]:
    """Apply YAML junction branch geometric rules to place node endpoints.

    Returns (constrained_endpoints, edge_endpoint_overrides).
    Mutates positions in-place.
    """
    edge_endpoint_overrides: dict[str, dict[str, tuple[float, float]]] = {}
    if not templates:
        return constrained_endpoints, edge_endpoint_overrides

    for _ in range(3):
        for node_id, template in templates.items():
            node_xy = positions.get(node_id)
            if node_xy is None:
                continue
            nx, ny = node_xy
            constrained_endpoints.add(node_id)
            ref_edge = artifact.edges.get(template.reference_edge)
            ref_u: tuple[float, float] | None = None
            if ref_edge is not None and len(ref_edge.connections) == 2:
                other_candidates = [
                    endpoint
                    for endpoint in ref_edge.connections
                    if endpoint != node_id and endpoint in positions
                ]
                if other_candidates:
                    ox, oy = positions[other_candidates[0]]
                    vx = nx - ox
                    vy = ny - oy
                    norm = math.hypot(vx, vy)
                    if norm > 1e-6:
                        ref_u = (vx / norm, vy / norm)
            for branch in template.branches:
                target = branch.target_endpoint
                constrained_endpoints.add(target)
                if target in fixed:
                    continue
                if ref_u is None:
                    positions[target] = _clamp_board(
                        (nx + float(branch.dx), ny + float(branch.dy)),
                        board_w,
                        board_h,
                    )
                    continue
                ux, uy = ref_u
                nx_left, ny_left = -uy, ux
                theta = math.radians(float(branch.angle_deg))
                bdx = ux * math.cos(theta) - uy * math.sin(theta)
                bdy = ux * math.sin(theta) + uy * math.cos(theta)
                token = (
                    (branch_offset_u_tokens or {}).get(branch.edge_id, "").strip()
                )
                branch_edge = artifact.edges.get(branch.edge_id)
                ref_w = (
                    float(ref_edge.width)
                    if ref_edge is not None and ref_edge.width is not None
                    else 0.0
                )
                branch_w = (
                    float(branch_edge.width)
                    if branch_edge is not None and branch_edge.width is not None
                    else 0.0
                )
                if branch.signed_v_kind.value == "edge_front" and token in (
                    "align_left",
                    "align_right",
                    "align_center",
                ):
                    lateral = 0.0
                    if token == "align_left":
                        lateral = (ref_w - branch_w) / 2.0
                    elif token == "align_right":
                        lateral = -(ref_w - branch_w) / 2.0
                    anchor = _clamp_board(
                        (nx + lateral * nx_left, ny + lateral * ny_left),
                        board_w,
                        board_h,
                    )
                elif branch.signed_v_kind.value == "edge_front" and token:
                    try:
                        lateral = float(token)
                    except ValueError:
                        lateral = float(branch.offset_u)
                    anchor = _clamp_board(
                        (nx + lateral * nx_left, ny + lateral * ny_left),
                        board_w,
                        board_h,
                    )
                elif branch.signed_v_kind.value in ("edge_left", "edge_right"):
                    # Side tangency: one edge of branch touches the reference
                    # side edge. In butt-cap rendering, branch start x/y is
                    # the branch side edge location, so lateral center offset
                    # must be Wref/2 (independent of branch width).
                    if ref_w > 0.0:
                        side_gap = ref_w / 2.0
                    else:
                        side_gap = abs(float(branch.signed_v))
                    side_sign = (
                        1.0 if branch.signed_v_kind.value == "edge_left" else -1.0
                    )
                    anchor = _clamp_board(
                        (
                            nx
                            + float(branch.offset_u) * ux
                            + side_sign * side_gap * nx_left,
                            ny
                            + float(branch.offset_u) * uy
                            + side_sign * side_gap * ny_left,
                        ),
                        board_w,
                        board_h,
                    )
                else:
                    anchor = _clamp_board(
                        (
                            nx
                            + float(branch.offset_u) * ux
                            + float(branch.signed_v) * nx_left,
                            ny
                            + float(branch.offset_u) * uy
                            + float(branch.signed_v) * ny_left,
                        ),
                        board_w,
                        board_h,
                    )
                branch_len = 1.0
                if (
                    branch_edge is not None
                    and branch_edge.target_length is not None
                ):
                    branch_len = max(float(branch_edge.target_length), 0.1)
                target_xy = _clamp_board(
                    (
                        anchor[0] + branch_len * bdx,
                        anchor[1] + branch_len * bdy,
                    ),
                    board_w,
                    board_h,
                )
                positions[target] = target_xy
                edge_endpoint_overrides.setdefault(branch.edge_id, {})[
                    node_id
                ] = anchor
                edge_endpoint_overrides.setdefault(branch.edge_id, {})[
                    target
                ] = target_xy

    return constrained_endpoints, edge_endpoint_overrides


def _propagate_constrained_edges(
    artifact: Any,
    positions: dict[str, tuple[float, float]],
    constrained_endpoints: set[str],
    fixed: set[str],
    board_w: float,
    board_h: float,
) -> set[str]:
    """Connectivity-first snap + iterative length-constrained propagation.

    Returns updated constrained_endpoints (positions mutated in-place).
    """
    # Step 1: snap virtual RLC pins to their connected trace endpoints
    for edge in artifact.edges.values():
        if (
            edge.edge_type != "microstrip"
            or len(edge.connections) != 2
            or edge.target_length is not None
        ):
            continue
        a_id, b_id = edge.connections
        a_kind = _prea_endpoint_kind(artifact, a_id)
        b_kind = _prea_endpoint_kind(artifact, b_id)
        if a_kind == "virtual_rlc_pin" and b_kind != "virtual_rlc_pin":
            positions[a_id] = positions[b_id]
            constrained_endpoints.add(a_id)
        elif b_kind == "virtual_rlc_pin" and a_kind != "virtual_rlc_pin":
            positions[b_id] = positions[a_id]
            constrained_endpoints.add(b_id)

    # Step 2: propagate constrained-length edges until convergence
    _changed = True
    while _changed:
        _changed = False
        for _edge in artifact.edges.values():
            if _edge.edge_type != "microstrip" or len(_edge.connections) != 2:
                continue
            if _edge.target_length is None:
                continue
            _a_id, _b_id = _edge.connections
            _desired = float(_edge.target_length)
            for _src_id, _dst_id in ((_a_id, _b_id), (_b_id, _a_id)):
                if _dst_id in constrained_endpoints or _dst_id in fixed:
                    continue
                if _src_id not in constrained_endpoints and _src_id not in fixed:
                    continue
                _sx, _sy = positions[_src_id]
                _dx_v = positions[_dst_id][0] - _sx
                _dy_v = positions[_dst_id][1] - _sy
                _norm = math.hypot(_dx_v, _dy_v)
                if _norm < 1e-6:
                    _dx_v, _dy_v, _norm = 0.0, -1.0, 1.0
                _ux_v = _dx_v / _norm
                _uy_v = _dy_v / _norm
                _new_pos = _clamp_board(
                    (_sx + _ux_v * _desired, _sy + _uy_v * _desired),
                    board_w,
                    board_h,
                )
                if positions.get(_dst_id) != _new_pos:
                    positions[_dst_id] = _new_pos
                    constrained_endpoints.add(_dst_id)
                    _changed = True

    # Step 3: re-apply connectivity locks so virtual endpoints remain snapped
    for edge in artifact.edges.values():
        if (
            edge.edge_type != "microstrip"
            or len(edge.connections) != 2
            or edge.target_length is not None
        ):
            continue
        a_id, b_id = edge.connections
        a_kind = _prea_endpoint_kind(artifact, a_id)
        b_kind = _prea_endpoint_kind(artifact, b_id)
        if a_kind == "virtual_rlc_pin" and b_kind != "virtual_rlc_pin":
            positions[a_id] = positions[b_id]
        elif b_kind == "virtual_rlc_pin" and a_kind != "virtual_rlc_pin":
            positions[b_id] = positions[a_id]

    return constrained_endpoints


def _solve_pre_a_positions(
    result: OrchestratorV2Result,
    *,
    board_w: float,
    board_h: float,
    junction_templates: dict[str, UniversalJunctionTemplate] | None = None,
    branch_offset_u_tokens: dict[str, str] | None = None,
) -> tuple[dict[str, tuple[float, float]], dict[str, dict[str, tuple[float, float]]]]:
    """Relax endpoint positions to satisfy target-length proportions before routing."""
    artifact = result.artifact
    plan_xy = dict(result.phase_a.plan.endpoint_xy)
    endpoints: set[str] = set()
    for edge in artifact.edges.values():
        endpoints.update(edge.connections)

    positions, fixed, constrained_endpoints = _seed_fixed_positions(
        artifact, plan_xy, endpoints, board_w, board_h
    )

    templates = junction_templates or {}
    constrained_endpoints, edge_endpoint_overrides = _apply_junction_templates(
        artifact,
        positions,
        fixed,
        constrained_endpoints,
        board_w,
        board_h,
        templates,
        branch_offset_u_tokens,
    )

    constrained_endpoints = _propagate_constrained_edges(
        artifact, positions, constrained_endpoints, fixed, board_w, board_h
    )

    # Enforce footprint pin pitch for 2-pin UV devices by moving the virtual
    # endpoint (and then its adjacent free-edge trace endpoint) instead of
    # stretching the device across long distances.
    locked_virtual: set[str] = set()

    def _endpoint_key_for(pin_endpoint: str) -> str | None:
        if pin_endpoint in positions:
            return pin_endpoint
        for key in positions:
            if "," not in key:
                continue
            members = [part.strip() for part in key.split(",") if part.strip()]
            if pin_endpoint in members:
                return key
        return None

    def _pitch_direction(
        anchor_ep: str,
        anchor_key: str,
        other_ep: str,
        other_key: str,
        anchor_xy: tuple[float, float],
        other_xy: tuple[float, float],
    ) -> tuple[float, float]:
        ax, ay = anchor_xy
        bx, by = other_xy
        dvx = bx - ax
        dvy = by - ay
        dnorm = math.hypot(dvx, dvy)
        if dnorm <= 1e-6:
            dvx, dvy, dnorm = 1.0, 0.0, 1.0
        down_u = (dvx / dnorm, dvy / dnorm)
        # Prefer downstream trend from the "other" pin when it already has a
        # constrained route leg (e.g. C4.PIN_2 -> TP5), so chained segments
        # continue toward sink direction instead of folding back.
        for edge in artifact.edges.values():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            if edge.target_length is None or float(edge.target_length) <= 0.0:
                continue
            a_id, b_id = edge.connections
            a_tokens = _expand_endpoint_tokens(a_id)
            b_tokens = _expand_endpoint_tokens(b_id)
            if other_ep in a_tokens or other_key == a_id:
                sink_id = b_id
            elif other_ep in b_tokens or other_key == b_id:
                sink_id = a_id
            else:
                continue
            if sink_id not in positions:
                continue
            sx, sy = positions[sink_id]
            dvx = sx - ax
            dvy = sy - ay
            dnorm = math.hypot(dvx, dvy)
            if dnorm <= 1e-6:
                continue
            down_u = (dvx / dnorm, dvy / dnorm)
            if sink_id in fixed:
                break

        ref_u: tuple[float, float] | None = None
        for edge in artifact.edges.values():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            if edge.target_length is None or float(edge.target_length) <= 0.0:
                continue
            a_id, b_id = edge.connections
            a_tokens = _expand_endpoint_tokens(a_id)
            b_tokens = _expand_endpoint_tokens(b_id)
            if anchor_ep in a_tokens or anchor_key == a_id:
                other_id = b_id
            elif anchor_ep in b_tokens or anchor_key == b_id:
                other_id = a_id
            else:
                continue
            if other_id not in positions:
                continue
            ox, oy = positions[other_id]
            ivx = ax - ox
            ivy = ay - oy
            inorm = math.hypot(ivx, ivy)
            if inorm <= 1e-6:
                continue
            ref_u = (ivx / inorm, ivy / inorm)
            break
        if ref_u is None:
            # If anchor endpoint is on a free edge (e.g. C1.PIN_1 on
            # seg4_to_C1R1), use the adjacent non-virtual node and inherit the
            # constrained segment direction at that node.
            for edge in artifact.edges.values():
                if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                    continue
                if edge.target_length is not None:
                    continue
                a_id, b_id = edge.connections
                a_tokens = _expand_endpoint_tokens(a_id)
                b_tokens = _expand_endpoint_tokens(b_id)
                if anchor_ep in a_tokens or anchor_key == a_id:
                    mid_id = b_id
                elif anchor_ep in b_tokens or anchor_key == b_id:
                    mid_id = a_id
                else:
                    continue
                if _prea_endpoint_kind(artifact, mid_id) == "virtual_rlc_pin":
                    continue
                if mid_id not in positions:
                    continue
                mx, my = positions[mid_id]
                for edge2 in artifact.edges.values():
                    if (
                        edge2.edge_type != "microstrip"
                        or len(edge2.connections) != 2
                        or edge2.target_length is None
                        or float(edge2.target_length) <= 0.0
                    ):
                        continue
                    c_id, d_id = edge2.connections
                    if c_id == mid_id:
                        other2 = d_id
                    elif d_id == mid_id:
                        other2 = c_id
                    else:
                        continue
                    if other2 not in positions:
                        continue
                    ox, oy = positions[other2]
                    ivx = mx - ox
                    ivy = my - oy
                    inorm = math.hypot(ivx, ivy)
                    if inorm <= 1e-6:
                        continue
                    ref_u = (ivx / inorm, ivy / inorm)
                    break
                if ref_u is not None:
                    break
        if ref_u is None:
            return down_u
        if abs(ref_u[0]) >= abs(ref_u[1]):
            ref_u = (1.0 if ref_u[0] >= 0.0 else -1.0, 0.0)
        else:
            ref_u = (0.0, 1.0 if ref_u[1] >= 0.0 else -1.0)
        ux, uy = ref_u
        # If the RLC sits in series between two constrained microstrips on both
        # anchor and other pins, prefer inline placement (straight through).
        _anchor_has_locked = any(
            e.edge_type == "microstrip"
            and e.target_length is not None
            and float(e.target_length) > 0.0
            and len(e.connections) == 2
            and (
                anchor_ep in _expand_endpoint_tokens(e.connections[0])
                or anchor_key == e.connections[0]
                or anchor_ep in _expand_endpoint_tokens(e.connections[1])
                or anchor_key == e.connections[1]
            )
            for e in artifact.edges.values()
        )
        _other_has_locked = any(
            e.edge_type == "microstrip"
            and e.target_length is not None
            and float(e.target_length) > 0.0
            and len(e.connections) == 2
            and (
                other_ep in _expand_endpoint_tokens(e.connections[0])
                or other_key == e.connections[0]
                or other_ep in _expand_endpoint_tokens(e.connections[1])
                or other_key == e.connections[1]
            )
            for e in artifact.edges.values()
        )
        if _anchor_has_locked and _other_has_locked:
            return (ux, uy)
        candidates = [(-uy, ux), (ux, uy), (uy, -ux)]  # left, front, right
        return max(candidates, key=lambda c: c[0] * down_u[0] + c[1] * down_u[1])

    def _connected_direction(known_ep: str, known_key: str) -> tuple[float, float]:
        kx, ky = positions[known_key]
        fallback: tuple[float, float] | None = None
        for edge in artifact.edges.values():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            a_id, b_id = edge.connections
            a_tokens = _expand_endpoint_tokens(a_id)
            b_tokens = _expand_endpoint_tokens(b_id)
            if known_ep in a_tokens or known_key == a_id:
                other_id = b_id
            elif known_ep in b_tokens or known_key == b_id:
                other_id = a_id
            else:
                continue
            if other_id not in positions:
                continue
            ox, oy = positions[other_id]
            vx = kx - ox
            vy = ky - oy
            norm = math.hypot(vx, vy)
            if norm <= 1e-6:
                continue
            cand = (vx / norm, vy / norm)
            if edge.target_length is not None and float(edge.target_length) > 0.0:
                return cand
            if fallback is None:
                fallback = cand
        return fallback if fallback is not None else (1.0, 0.0)

    # Seed missing endpoint coordinates for 2-pin UV parts so both pins are
    # always renderable (e.g. C3/C5/C6 GND side), then normal pitch-lock logic
    # can further refine them.
    for comp_id, uv in artifact.uv_components.items():
        if len(uv.pads) != 2:
            continue
        pin_to_local = {
            pad.pin: (float(pad.local_x), float(pad.local_y)) for pad in uv.pads
        }
        pin_names = list(pin_to_local.keys())
        anchor_pin = (
            uv.uv_meta.anchor_pin
            if uv.uv_meta is not None and uv.uv_meta.anchor_pin in pin_to_local
            else pin_names[0]
        )
        other_pin = next((pin for pin in pin_names if pin != anchor_pin), None)
        if other_pin is None:
            continue
        anchor_ep = f"{comp_id}.{anchor_pin}"
        other_ep = f"{comp_id}.{other_pin}"
        anchor_key = _endpoint_key_for(anchor_ep)
        other_key = _endpoint_key_for(other_ep)
        if (anchor_key is None) == (other_key is None):
            continue
        pitch = math.hypot(
            pin_to_local[other_pin][0] - pin_to_local[anchor_pin][0],
            pin_to_local[other_pin][1] - pin_to_local[anchor_pin][1],
        )
        if pitch <= 1e-6:
            continue
        if anchor_key is not None:
            known_ep, known_key, missing_ep = anchor_ep, anchor_key, other_ep
        else:
            known_ep, known_key, missing_ep = other_ep, other_key or "", anchor_ep
            if not known_key:
                continue
        ux, uy = _connected_direction(known_ep, known_key)
        kx, ky = positions[known_key]
        positions[missing_ep] = _clamp_board(
            (kx + ux * pitch, ky + uy * pitch), board_w, board_h
        )

    for comp_id, uv in artifact.uv_components.items():
        if len(uv.pads) != 2:
            continue
        pin_to_local = {
            pad.pin: (float(pad.local_x), float(pad.local_y)) for pad in uv.pads
        }
        pin_names = list(pin_to_local.keys())
        anchor_pin = (
            uv.uv_meta.anchor_pin
            if uv.uv_meta is not None and uv.uv_meta.anchor_pin in pin_to_local
            else pin_names[0]
        )
        other_pin = next((pin for pin in pin_names if pin != anchor_pin), None)
        if other_pin is None:
            continue
        anchor_ep = f"{comp_id}.{anchor_pin}"
        other_ep = f"{comp_id}.{other_pin}"
        anchor_key = _endpoint_key_for(anchor_ep)
        other_key = _endpoint_key_for(other_ep)
        if anchor_key is None or other_key is None:
            continue
        ax, ay = positions[anchor_key]
        bx, by = positions[other_key]
        pitch = math.hypot(
            pin_to_local[other_pin][0] - pin_to_local[anchor_pin][0],
            pin_to_local[other_pin][1] - pin_to_local[anchor_pin][1],
        )
        if pitch <= 1e-6:
            continue
        ux, uy = _pitch_direction(
            anchor_ep, anchor_key, other_ep, other_key, (ax, ay), (bx, by)
        )
        moved = _clamp_board((ax + ux * pitch, ay + uy * pitch), board_w, board_h)
        positions[other_key] = moved
        positions[other_ep] = moved
        positions[anchor_ep] = (ax, ay)
        locked_virtual.update((anchor_ep, other_ep, anchor_key, other_key))

    for endpoint in list(positions.keys()):
        if "," not in endpoint:
            continue
        members = [part.strip() for part in endpoint.split(",") if part.strip()]
        coords = [positions[m] for m in members if m in positions]
        if not coords:
            continue
        positions[endpoint] = (
            sum(x for x, _ in coords) / len(coords),
            sum(y for _, y in coords) / len(coords),
        )
        if members and all(member in locked_virtual for member in members):
            locked_virtual.add(endpoint)

    for edge in artifact.edges.values():
        if (
            edge.edge_type != "microstrip"
            or len(edge.connections) != 2
            or edge.target_length is not None
        ):
            continue
        a_id, b_id = edge.connections
        a_kind = _prea_endpoint_kind(artifact, a_id)
        b_kind = _prea_endpoint_kind(artifact, b_id)
        if (
            a_kind == "virtual_rlc_pin"
            and a_id in locked_virtual
            and b_kind != "virtual_rlc_pin"
        ):
            positions[b_id] = positions[a_id]
        elif (
            b_kind == "virtual_rlc_pin"
            and b_id in locked_virtual
            and a_kind != "virtual_rlc_pin"
        ):
            positions[a_id] = positions[b_id]

    # Propagate constrained segment length from non-fixed trace endpoints toward
    # virtual pins (chain-first), so seg5/seg6-like edges set the UV anchor
    # location instead of leaving long stretched hops.
    def _virtual_downstream_u(
        dst_id: str, src_xy: tuple[float, float]
    ) -> tuple[float, float] | None:
        tokens = _expand_endpoint_tokens(dst_id)
        if len(tokens) != 1:
            return None
        endpoint = tokens[0]
        if "." not in endpoint:
            return None
        comp_id, pin = endpoint.split(".", 1)
        uv = artifact.uv_components.get(comp_id)
        if uv is None or len(uv.pads) != 2:
            return None
        peer_pin = next((pad.pin for pad in uv.pads if pad.pin != pin), None)
        if peer_pin is None:
            return None
        peer_ep = f"{comp_id}.{peer_pin}"
        sx, sy = src_xy
        preferred: tuple[float, float] | None = None
        for edge in artifact.edges.values():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            if edge.target_length is None or float(edge.target_length) <= 0.0:
                continue
            a_id, b_id = edge.connections
            a_tokens = _expand_endpoint_tokens(a_id)
            b_tokens = _expand_endpoint_tokens(b_id)
            if peer_ep in a_tokens:
                sink_id = b_id
            elif peer_ep in b_tokens:
                sink_id = a_id
            else:
                continue
            if sink_id not in positions:
                continue
            tx, ty = positions[sink_id]
            dvx = tx - sx
            dvy = ty - sy
            dnorm = math.hypot(dvx, dvy)
            if dnorm <= 1e-6:
                continue
            cand = (dvx / dnorm, dvy / dnorm)
            if sink_id in fixed:
                return cand
            if preferred is None:
                preferred = cand
        return preferred

    for edge in artifact.edges.values():
        if (
            edge.edge_type != "microstrip"
            or len(edge.connections) != 2
            or edge.target_length is None
            or float(edge.target_length) <= 0.0
        ):
            continue
        a_id, b_id = edge.connections
        for src_id, dst_id in ((a_id, b_id), (b_id, a_id)):
            src_kind = _prea_endpoint_kind(artifact, src_id)
            dst_kind = _prea_endpoint_kind(artifact, dst_id)
            if src_id in fixed:
                continue
            if src_kind == "virtual_rlc_pin" or dst_kind != "virtual_rlc_pin":
                continue
            sx, sy = positions[src_id]
            dx = positions[dst_id][0] - sx
            dy = positions[dst_id][1] - sy
            norm = math.hypot(dx, dy)
            hint_u = _virtual_downstream_u(dst_id, (sx, sy))
            if hint_u is not None:
                ux, uy = hint_u
            elif norm <= 1e-6:
                ux, uy = 1.0, 0.0
            else:
                ux, uy = dx / norm, dy / norm
            target_len = float(edge.target_length)
            moved = _clamp_board(
                (sx + ux * target_len, sy + uy * target_len), board_w, board_h
            )
            positions[dst_id] = moved
            for token in _expand_endpoint_tokens(dst_id):
                positions[token] = moved

    # Re-lock package pitch after constrained propagation.
    for comp_id, uv in artifact.uv_components.items():
        if len(uv.pads) != 2:
            continue
        pin_to_local = {
            pad.pin: (float(pad.local_x), float(pad.local_y)) for pad in uv.pads
        }
        pin_names = list(pin_to_local.keys())
        anchor_pin = (
            uv.uv_meta.anchor_pin
            if uv.uv_meta is not None and uv.uv_meta.anchor_pin in pin_to_local
            else pin_names[0]
        )
        other_pin = next((pin for pin in pin_names if pin != anchor_pin), None)
        if other_pin is None:
            continue
        anchor_ep = f"{comp_id}.{anchor_pin}"
        other_ep = f"{comp_id}.{other_pin}"
        anchor_key = _endpoint_key_for(anchor_ep)
        other_key = _endpoint_key_for(other_ep)
        if anchor_key is None or other_key is None:
            continue
        ax, ay = positions[anchor_key]
        bx, by = positions[other_key]
        pitch = math.hypot(
            pin_to_local[other_pin][0] - pin_to_local[anchor_pin][0],
            pin_to_local[other_pin][1] - pin_to_local[anchor_pin][1],
        )
        if pitch <= 1e-6:
            continue
        ux, uy = _pitch_direction(
            anchor_ep, anchor_key, other_ep, other_key, (ax, ay), (bx, by)
        )
        moved = _clamp_board((ax + ux * pitch, ay + uy * pitch), board_w, board_h)
        positions[other_key] = moved
        positions[other_ep] = moved

    endpoint_edge_ids: dict[str, set[str]] = {}
    for edge_name, edge in artifact.edges.items():
        if edge.edge_type != "microstrip" or len(edge.connections) != 2:
            continue
        for endpoint in edge.connections:
            for token in _expand_endpoint_tokens(endpoint):
                endpoint_edge_ids.setdefault(token, set()).add(edge_name)

    def _point_to_segment_distance(
        px: float, py: float, a: tuple[float, float], b: tuple[float, float]
    ) -> float:
        ax, ay = a
        bx, by = b
        vx = bx - ax
        vy = by - ay
        seg2 = vx * vx + vy * vy
        if seg2 <= 1e-12:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * vx + (py - ay) * vy) / seg2
        t = max(0.0, min(1.0, t))
        cx = ax + t * vx
        cy = ay + t * vy
        return math.hypot(px - cx, py - cy)

    def _one_hop_constrained_u(known_ep: str) -> tuple[float, float] | None:
        for edge in artifact.edges.values():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            if edge.target_length is not None:
                continue
            a_id, b_id = edge.connections
            a_tokens = _expand_endpoint_tokens(a_id)
            b_tokens = _expand_endpoint_tokens(b_id)
            if known_ep in a_tokens:
                mid_id = b_id
            elif known_ep in b_tokens:
                mid_id = a_id
            else:
                continue
            if _prea_endpoint_kind(artifact, mid_id) == "virtual_rlc_pin":
                continue
            if mid_id not in positions:
                continue
            mx, my = positions[mid_id]
            for edge2 in artifact.edges.values():
                if (
                    edge2.edge_type != "microstrip"
                    or len(edge2.connections) != 2
                    or edge2.target_length is None
                    or float(edge2.target_length) <= 0.0
                ):
                    continue
                c_id, d_id = edge2.connections
                if c_id == mid_id:
                    other2 = d_id
                elif d_id == mid_id:
                    other2 = c_id
                else:
                    continue
                if other2 not in positions:
                    continue
                ox, oy = positions[other2]
                vx = mx - ox
                vy = my - oy
                norm = math.hypot(vx, vy)
                if norm <= 1e-6:
                    continue
                return (vx / norm, vy / norm)
        return None

    # For 2-pin UV components where only one pin has explicit edge connectivity
    # (e.g. C3/C5/C6 shunt caps), enforce the same left/front/right candidate
    # family used elsewhere and pick a non-overlapping axis-aligned direction.
    constrained_segments: list[tuple[str, tuple[float, float], tuple[float, float]]] = (
        []
    )
    for edge_name, edge in artifact.edges.items():
        if (
            edge.edge_type != "microstrip"
            or len(edge.connections) != 2
            or edge.target_length is None
            or float(edge.target_length) <= 0.0
        ):
            continue
        s_id, e_id = edge.connections
        if s_id not in positions or e_id not in positions:
            continue
        constrained_segments.append((edge_name, positions[s_id], positions[e_id]))

    for comp_id, uv in artifact.uv_components.items():
        if len(uv.pads) != 2:
            continue
        pin_to_local = {
            pad.pin: (float(pad.local_x), float(pad.local_y)) for pad in uv.pads
        }
        pin_names = list(pin_to_local.keys())
        if len(pin_names) != 2:
            continue
        ep_a = f"{comp_id}.{pin_names[0]}"
        ep_b = f"{comp_id}.{pin_names[1]}"
        cnt_a = len(endpoint_edge_ids.get(ep_a, set()))
        cnt_b = len(endpoint_edge_ids.get(ep_b, set()))
        if not ((cnt_a > 0 and cnt_b == 0) or (cnt_b > 0 and cnt_a == 0)):
            continue
        known_ep, missing_ep = (ep_a, ep_b) if cnt_a > 0 else (ep_b, ep_a)
        if known_ep not in positions or missing_ep not in positions:
            continue
        known_pin, missing_pin = known_ep.split(".", 1)[1], missing_ep.split(".", 1)[1]
        pitch = math.hypot(
            pin_to_local[missing_pin][0] - pin_to_local[known_pin][0],
            pin_to_local[missing_pin][1] - pin_to_local[known_pin][1],
        )
        if pitch <= 1e-6:
            continue
        ref_u = _one_hop_constrained_u(known_ep)
        if ref_u is None:
            ref_u = _connected_direction(known_ep, known_ep)
        ux, uy = ref_u
        candidates = [(-uy, ux), (ux, uy), (uy, -ux)]  # left, front, right
        kx, ky = positions[known_ep]

        def _candidate_score(vec: tuple[float, float]) -> tuple[float, float]:
            tx = kx + vec[0] * pitch
            ty = ky + vec[1] * pitch
            clearances = []
            for edge_name, a_xy, b_xy in constrained_segments:
                if edge_name in endpoint_edge_ids.get(known_ep, set()):
                    continue
                clearances.append(_point_to_segment_distance(tx, ty, a_xy, b_xy))
            clearance = min(clearances) if clearances else 1e6
            return (clearance, abs(vec[0]) + abs(vec[1]))

        best_vec = max(candidates, key=_candidate_score)
        positions[missing_ep] = _clamp_board(
            (kx + best_vec[0] * pitch, ky + best_vec[1] * pitch), board_w, board_h
        )

    # Expand composite endpoints like "C1.PIN_1,R1.PIN_1" so each member pin
    # has a concrete coordinate for preA footprint rendering.
    for endpoint, xy in list(positions.items()):
        if "," not in endpoint:
            continue
        for token in (part.strip() for part in endpoint.split(",")):
            if token and token not in positions:
                positions[token] = xy
    return positions, edge_endpoint_overrides


def _prea_constraint_provenance(
    result: OrchestratorV2Result,
    templates: dict[str, UniversalJunctionTemplate],
) -> list[dict[str, Any]]:
    strict_edges = {
        branch.edge_id
        for template in templates.values()
        for branch in template.branches
    }
    out: list[dict[str, Any]] = []
    for edge in sorted(result.artifact.edges.values(), key=lambda e: e.name):
        mode = "strict_junction_rule" if edge.name in strict_edges else "relaxed"
        out.append(
            {
                "edge_id": edge.name,
                "mode": mode,
                "connections": list(edge.connections),
            }
        )
    return out


def _persist_phase_artefacts(
    result: OrchestratorV2Result,
    out_dir: Path,
    *,
    layout_path: Path,
    layout: V33Layout | None = None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    project = result.geometry.project
    artefacts: dict[str, Path] = {}

    summary = phase_summary(result)
    summary_path = out_dir / f"{project}.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    artefacts["summary"] = summary_path

    # Pre-Phase-A: raw YAML topology connectivity snapshot.
    pre_a_svg = out_dir / f"{project}.preA.svg"
    templates = _load_junction_templates(layout_path=layout_path)
    branch_offset_u_tokens = _load_branch_offset_u_tokens(layout_path=layout_path)
    pre_positions, virtual_endpoints, pre_edge_overrides = _render_pre_phase_svg(
        result,
        pre_a_svg,
        templates=templates,
        branch_offset_u_tokens=branch_offset_u_tokens,
        layout=layout,
        banner=_phase_banner(
            "Pre-A",
            "YAML-defined physical connectivity + UV footprints (no routing)",
            "#7c3aed",
        ),
    )
    artefacts["preA_svg"] = pre_a_svg

    pre_a_json = out_dir / f"{project}.preA.json"
    pre_a_json.write_text(
        json.dumps(
            {
                "edges": [
                    {
                        "edge_id": edge.name,
                        "edge_short": _prea_short_name(edge.name),
                        "routing_class": edge.routing_class,
                        "connections": list(edge.connections),
                        "connections_short": [
                            _prea_short_name(endpoint) for endpoint in edge.connections
                        ],
                        "width": edge.width,
                        "target_length": edge.target_length,
                        "endpoint_positions_mm": {
                            endpoint: {
                                "x": pre_positions.get(endpoint, (0.0, 0.0))[0],
                                "y": pre_positions.get(endpoint, (0.0, 0.0))[1],
                                "kind": _prea_endpoint_kind(result.artifact, endpoint),
                            }
                            for endpoint in edge.connections
                        },
                        "render_endpoint_positions_mm": {
                            endpoint: {
                                "x": pre_edge_overrides.get(edge.name, {}).get(
                                    endpoint, pre_positions.get(endpoint, (0.0, 0.0))
                                )[0],
                                "y": pre_edge_overrides.get(edge.name, {}).get(
                                    endpoint, pre_positions.get(endpoint, (0.0, 0.0))
                                )[1],
                            }
                            for endpoint in edge.connections
                        },
                    }
                    for edge in result.artifact.edges.values()
                ],
                "virtual_endpoints": sorted(virtual_endpoints),
                "yaml_geometric_constraints_applied": _prea_constraint_provenance(
                    result, templates
                ),
            },
            indent=2,
            default=str,
        )
    )
    artefacts["preA"] = pre_a_json

    # Per-phase JSON (Phase A and B are the meaningful ones).
    phase_a_path = out_dir / f"{project}.phaseA.json"
    phase_a_path.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "edge_id": r.edge_id,
                        "polyline_um": r.polyline_um,
                        "length_mm": r.length_mm,
                        "target_mm": r.target_mm,
                        "length_err_pct": r.length_err_pct,
                        "success": r.success,
                        "rip_up_round": r.rip_up_round,
                        "failure_reason": r.failure_reason,
                    }
                    for r in result.phase_a.skeleton.routes.values()
                ],
                "rip_up_rounds": result.phase_a.skeleton.rip_up_rounds,
                "wall_seconds": result.phase_a.wall_seconds,
            },
            indent=2,
            default=str,
        )
    )
    artefacts["phaseA"] = phase_a_path

    phase_b_path = out_dir / f"{project}.phaseB.json"
    phase_b_path.write_text(
        json.dumps(
            {
                "placements": {
                    name: {
                        "anchor_x": p.anchor.x,
                        "anchor_y": p.anchor.y,
                        "rotation_deg": p.rotation_deg,
                        "pads": [
                            {"pin": pp.pin, "x": pp.point.x, "y": pp.point.y}
                            for pp in p.pads
                        ],
                    }
                    for name, p in result.phase_b.adhesion.placements.items()
                },
                "failed": result.phase_b.adhesion.failed,
                "wall_seconds": result.phase_b.wall_seconds,
            },
            indent=2,
            default=str,
        )
    )
    artefacts["phaseB"] = phase_b_path

    # Per-phase SVG snapshots
    routed_ok, routed_total = result.phase_a.skeleton.success_rate()
    uv_names = set(result.phase_b.adhesion.placements.keys())
    uv_placed = len(uv_names)
    uv_total = len(result.artifact.uv_components)
    flex_ok = len(result.phase_c.routed_flex_edges)
    flex_failed = len(result.phase_c.failed_flex_edges)

    phase_a_svg = out_dir / f"{project}.phaseA.svg"
    phase_a_geom = _build_phase_a_geom(result)
    _render_svg(
        phase_a_geom,
        phase_a_svg,
        banner=_phase_banner(
            "Phase A",
            f"Skeleton routing — {routed_ok}/{routed_total} microstrips routed | UV not yet placed",
            "#b45309",
        ),
        overlay=_pin_label_overlay(phase_a_geom),
        layout=layout,
    )
    artefacts["phaseA_svg"] = phase_a_svg

    geom_b = _build_phase_b_geom(result)
    phase_b_svg = out_dir / f"{project}.phaseB.svg"
    _render_svg(
        geom_b,
        phase_b_svg,
        banner=_phase_banner(
            "Phase B",
            f"UV adhesion — {uv_placed}/{uv_total} components placed (highlighted green)",
            "#15803d",
        ),
        overlay=_uv_highlight_overlay(geom_b, uv_names),
        layout=layout,
    )
    artefacts["phaseB_svg"] = phase_b_svg

    # Phase C SVG = final geometry (flex routes already in result.geometry)
    phase_c_svg = out_dir / f"{project}.phaseC.svg"
    _render_svg(
        result.geometry,
        phase_c_svg,
        banner=_phase_banner(
            "Phase C",
            f"Flex routing — {flex_ok} routed / {flex_failed} failed",
            "#1d4ed8",
        ),
        layout=layout,
    )
    artefacts["phaseC_svg"] = phase_c_svg

    return artefacts


def _emit_svg(
    result: OrchestratorV2Result, svg_path: Path, *, layout: V33Layout | None = None
) -> None:
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_text = render_full_layout(result.geometry, layout=layout)
    svg_path.write_text(svg_text)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    options = OrchestratorV2Options(
        clearance_mm=args.clearance_mm,
        grid_step_um=args.grid_step_um,
        rip_up_rounds=args.rip_up_rounds,
        out_dir=args.out_dir,
    )
    result = solve_layout_v2(args.layout, options=options)

    layout = load_v33_layout(args.layout)
    artefacts = _persist_phase_artefacts(
        result, args.out_dir, layout_path=Path(args.layout), layout=layout
    )

    svg_path = args.svg_out or (args.out_dir / f"{result.geometry.project}.final.svg")
    _emit_svg(result, svg_path, layout=layout)
    artefacts["final_svg"] = svg_path

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(phase_summary(result), indent=2, default=str)
        )

    routed_ok, routed_total = result.phase_a.skeleton.success_rate()
    uv_placed = len(result.phase_b.adhesion.placements)
    uv_total = len(result.artifact.uv_components)
    wall_total = result.geometry.solve_wall_seconds
    if not args.quiet:
        print(
            f"[v2] project={result.geometry.project} "
            f"status={result.geometry.solve_status} "
            f"phase_a={routed_ok}/{routed_total} "
            f"uv={uv_placed}/{uv_total} "
            f"wall={wall_total:.2f}s"
        )
        for name, path in artefacts.items():
            print(f"  {name}: {path}")

    return 0 if routed_ok > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
