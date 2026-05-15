"""``pcb_solve_v2`` CLI — M10 skeleton-first three-phase pipeline.

Replacement for the v6 ``pcb_solve``: runs the new
``Frontend → Phase A skeleton routing → Phase B UV adhesion → Phase C
flex (no-op for PA) → Postproc`` flow and emits per-phase SVG/JSON
artefacts so each stage can be reviewed independently.
"""

from __future__ import annotations

import argparse
import json
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
from topology.loaders import load_topology_graph
from topology.render_svg import render_topology_svg


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


def _render_pre_phase_svg(yaml_path: Path, path: Path, *, banner: str = "") -> None:
    """Render YAML-defined connectivity SVG before any Phase A routing."""
    graph = load_topology_graph(yaml_path)
    svg = render_topology_svg(graph)
    if banner:
        svg = svg.replace("</svg>", banner + "</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


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
    _render_pre_phase_svg(
        layout_path,
        pre_a_svg,
        banner=_phase_banner(
            "Pre-A",
            "YAML-defined microstrip connectivity (before Phase A routing)",
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
                    }
                    for edge in result.artifact.edges.values()
                ]
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
