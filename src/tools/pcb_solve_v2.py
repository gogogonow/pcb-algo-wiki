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
from xml.sax.saxutils import escape

from output import render_full_layout
from schema.geometry_ir import GeometryIR
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
    geom: GeometryIR, path: Path, *, banner: str = "", overlay: str = ""
) -> None:
    """Write a rendered SVG to *path*, injecting an optional phase banner and overlay."""
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = render_full_layout(geom)
    if overlay:
        svg = svg.replace("</svg>", overlay + "</svg>")
    if banner:
        svg = svg.replace("</svg>", banner + "</svg>")
    path.write_text(svg)


def _render_pre_phase_svg(
    result: OrchestratorV2Result,
    path: Path,
    *,
    banner: str = "",
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """Render physical Pre-A view in board coordinates.

    Shows board outline + fixed points + microstrip connectivity with width/length labels.
    RLC devices are hidden; endpoints connected to RLC pins are rendered as virtual markers.
    Returns endpoint positions (mm) and the virtual endpoint id set for JSON sidecar output.
    """
    artifact = result.artifact
    board_w = float(artifact.board.get("width", 40.0))
    board_h = float(artifact.board.get("height", 100.0))
    positions = _solve_pre_a_positions(result, board_w=board_w, board_h=board_h)
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
        ".prea-edge{fill:none;stroke-linecap:round;opacity:0.95;}",
        ".prea-label{fill:#0f172a;font-family:Arial,sans-serif;font-size:9px;}",
        "</style>",
        f'<rect class="prea-board" x="{_x(0):.2f}" y="{_y(board_h):.2f}" width="{board_w * px_per_mm:.2f}" height="{board_h * px_per_mm:.2f}"/>',
    ]

    # Draw microstrip edges with width/length labels.
    for edge_id, edge in sorted(artifact.edges.items()):
        if edge.edge_type != "microstrip" or len(edge.connections) != 2:
            continue
        start_id, end_id = edge.connections
        if start_id not in positions or end_id not in positions:
            continue
        sx, sy = positions[start_id]
        ex, ey = positions[end_id]
        stroke_width = max(float(edge.width or 0.2) * px_per_mm, 1.2)
        lines.append(
            f'<line class="prea-edge" data-edge-id="{escape(edge_id)}" '
            f'x1="{_x(sx):.2f}" y1="{_y(sy):.2f}" x2="{_x(ex):.2f}" y2="{_y(ey):.2f}" '
            f'stroke="#334155" stroke-width="{stroke_width:.2f}"/>'
        )
        mx = (sx + ex) / 2.0
        my = (sy + ey) / 2.0
        length_text = (
            f"L={float(edge.target_length):.1f}mm"
            if edge.target_length is not None
            else "L=n/a"
        )
        width_text = f"w={float(edge.width or 0.0):.1f}mm"
        lines.append(
            f'<text class="prea-label" x="{_x(mx)+4:.2f}" y="{_y(my)-4:.2f}">'
            f"{escape(edge_id)} | {escape(width_text)} | {escape(length_text)}</text>"
        )

    # Draw fixed points / node points / virtual endpoints.
    for endpoint, (px, py) in sorted(positions.items()):
        kind = _prea_endpoint_kind(artifact, endpoint)
        if kind == "fixed_pin":
            lines.append(
                f'<circle class="prea-fixed-point" cx="{_x(px):.2f}" cy="{_y(py):.2f}" r="3.2"/>'
            )
            lines.append(
                f'<text class="prea-label" x="{_x(px)+4:.2f}" y="{_y(py)-4:.2f}">{escape(endpoint)}</text>'
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
                f'<text class="prea-label" x="{cx+4:.2f}" y="{cy-4:.2f}">{escape(endpoint)}</text>'
            )

    if banner:
        lines.append(banner)
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return positions, virtual_endpoints


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


def _solve_pre_a_positions(
    result: OrchestratorV2Result, *, board_w: float, board_h: float
) -> dict[str, tuple[float, float]]:
    """Relax endpoint positions to satisfy target-length proportions before routing."""
    artifact = result.artifact
    plan_xy = dict(result.phase_a.plan.endpoint_xy)
    endpoints: set[str] = set()
    for edge in artifact.edges.values():
        endpoints.update(edge.connections)

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

    def _clamp(p: tuple[float, float]) -> tuple[float, float]:
        return (min(max(p[0], 0.0), board_w), min(max(p[1], 0.0), board_h))

    for _ in range(180):
        for edge in artifact.edges.values():
            if edge.edge_type != "microstrip" or len(edge.connections) != 2:
                continue
            a_id, b_id = edge.connections
            ax, ay = positions[a_id]
            bx, by = positions[b_id]
            dx = bx - ax
            dy = by - ay
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                dx, dy, dist = 1e-3, 0.0, 1e-3
            desired = (
                float(edge.target_length) if edge.target_length is not None else dist
            )
            delta = (desired - dist) / 2.0
            ux = dx / dist
            uy = dy / dist
            move_a = (-ux * delta, -uy * delta)
            move_b = (ux * delta, uy * delta)
            if a_id in fixed and b_id in fixed:
                continue
            if a_id in fixed:
                positions[b_id] = _clamp((bx + 2 * move_b[0], by + 2 * move_b[1]))
            elif b_id in fixed:
                positions[a_id] = _clamp((ax + 2 * move_a[0], ay + 2 * move_a[1]))
            else:
                positions[a_id] = _clamp((ax + move_a[0], ay + move_a[1]))
                positions[b_id] = _clamp((bx + move_b[0], by + move_b[1]))
    return positions


def _persist_phase_artefacts(
    result: OrchestratorV2Result,
    out_dir: Path,
    *,
    layout_path: Path,
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
    pre_positions, virtual_endpoints = _render_pre_phase_svg(
        result,
        pre_a_svg,
        banner=_phase_banner(
            "Pre-A",
            "YAML-defined physical connectivity (no routing / no RLC placement)",
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
                        "routing_class": edge.routing_class,
                        "connections": list(edge.connections),
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
                    }
                    for edge in result.artifact.edges.values()
                ],
                "virtual_endpoints": sorted(virtual_endpoints),
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
    )
    artefacts["phaseC_svg"] = phase_c_svg

    return artefacts


def _emit_svg(result: OrchestratorV2Result, svg_path: Path) -> None:
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_text = render_full_layout(result.geometry)
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

    artefacts = _persist_phase_artefacts(
        result, args.out_dir, layout_path=Path(args.layout)
    )

    svg_path = args.svg_out or (args.out_dir / f"{result.geometry.project}.final.svg")
    _emit_svg(result, svg_path)
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
