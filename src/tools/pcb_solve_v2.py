"""``pcb_solve_v2`` CLI — M10 skeleton-first three-phase pipeline.

Replacement for the v6 ``pcb_solve``: runs the new
``Frontend → Phase A skeleton routing → Phase B UV adhesion → Phase C
flex (no-op for PA) → Postproc`` flow and emits per-phase SVG/JSON
artefacts so each stage can be reviewed independently.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from output import render_full_layout
from frontend.solver_ir import compile_solver_ir
from frontend.compile import FrontendArtifact  # re-exported via compile module
from schema.geometry_ir import GeometryIR
from schema.solver_ir import UniversalJunctionTemplate
from schema.v33 import V33Layout, load_v33_layout
from solver.v2 import OrchestratorV2Options, solve_layout_v2
from solver.v2.orchestrator import (
    _assemble_geometry,
    compile_and_plan,
    OrchestratorV2Result,
    phase_summary,
    PhaseAResult,
    PhaseBResult,
    PhaseCResult,
    run_phase_a,
    run_phase_b,
    run_phase_c,
)
from solver.v2.skeleton_router import SkeletonReport
from solver.v2.uv_adhesion import UvAdhesionReport

logger = logging.getLogger(__name__)


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
    p.add_argument(
        "--stop-after",
        choices=["preA", "phaseA", "phaseB", "phaseC"],
        default="phaseC",
        help=(
            "Stop the pipeline after the specified phase and emit artefacts "
            "(default: phaseC = run full pipeline)."
        ),
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


def _make_viewer_components(artifact: "FrontendArtifact") -> list[dict]:
    """Convert FrontendArtifact components to viewer.json component list."""
    KIND_MAP = {"parametric_uv": "uv"}
    result_list = []
    for ref, comp in artifact.components.items():
        if comp.bbox is not None:
            x_mm = round((comp.bbox.min_x + comp.bbox.max_x) / 2.0, 4)
            y_mm = round((comp.bbox.min_y + comp.bbox.max_y) / 2.0, 4)
            w_mm = round(comp.bbox.max_x - comp.bbox.min_x, 4)
            h_mm = round(comp.bbox.max_y - comp.bbox.min_y, 4)
        else:
            x_mm, y_mm, w_mm, h_mm = 0.0, 0.0, 1.0, 1.0
        kind = KIND_MAP.get(comp.placement_kind, comp.placement_kind)
        pads = [
            {"pin": p.pin, "x_mm": round(p.abs_x, 4), "y_mm": round(p.abs_y, 4)}
            for p in comp.pads
            if p.abs_x is not None and p.abs_y is not None
        ]
        result_list.append(
            {
                "ref": ref,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "w_mm": w_mm,
                "h_mm": h_mm,
                "rotation_deg": 0.0,
                "kind": kind,
                "pads": pads,
            }
        )
    return result_list


def _collect_skeleton_routes(
    skeleton: "SkeletonReport",
    artifact: "FrontendArtifact",
) -> list[dict]:
    """Convert SkeletonReport routes (µm) to viewer.json route list (mm)."""
    UM_TO_MM = 1.0 / 1000.0
    routes = []
    for edge_id, r in skeleton.routes.items():
        edge = artifact.edges.get(edge_id)
        width_mm = round(float(edge.width), 4) if (edge and edge.width) else 0.0
        target_mm = round(float(r.target_mm), 4) if r.target_mm is not None else None
        polyline_mm = [
            [round(x * UM_TO_MM, 4), round(y * UM_TO_MM, 4)] for x, y in r.polyline_um
        ]
        routes.append(
            {
                "edge_id": edge_id,
                "polyline_mm": polyline_mm,
                "width_mm": width_mm,
                "success": r.success,
                "length_mm": round(r.length_mm, 4),
                "target_mm": target_mm,
                "length_err_pct": (
                    round(r.length_err_pct, 2) if r.length_err_pct is not None else None
                ),
                "failure_reason": r.failure_reason,
                "rip_up_round": r.rip_up_round,
            }
        )
    return routes


def _emit_viewer_bundle(
    result: "OrchestratorV2Result",
    out_dir: Path,
    *,
    layout: "V33Layout | None" = None,
) -> Path:
    """Emit {project}.viewer.json — single-file bundle for the PixiJS viewer."""
    _ = layout  # Reserved for future extension
    project = result.geometry.project
    board = result.geometry.board

    # --- preA: edge connectivity graph ---
    # Use preA solver coordinates (not phaseA skeleton endpoint snapshot), so
    # preA can render all branches even when some edges are unrouted in phaseA.
    pre_positions, pre_edge_overrides = _solve_pre_a_positions(
        result,
        board_w=float(board.width),
        board_h=float(board.height),
    )
    prea_edges = []
    for edge_id, edge in result.artifact.edges.items():
        ep_positions: dict[str, dict] = {}
        for ep in edge.connections:
            xy = pre_edge_overrides.get(edge_id, {}).get(ep, pre_positions.get(ep))
            if xy is not None:
                ep_positions[ep] = {
                    "x": round(float(xy[0]), 4),
                    "y": round(float(xy[1]), 4),
                }
        prea_edges.append(
            {
                "edge_id": edge_id,
                "routing_class": edge.routing_class,
                "width_mm": round(float(edge.width), 4) if edge.width else None,
                "target_length_mm": (
                    round(float(edge.target_length), 4) if edge.target_length else None
                ),
                "connections": list(edge.connections),
                "endpoint_positions_mm": ep_positions,
            }
        )

    # --- preA: UV/RLC estimated placements from preA endpoint solve ---
    pre_uv_placements = []
    for name, comp in result.artifact.uv_components.items():
        pad_points: list[tuple[str, float, float]] = []
        for p in comp.pads:
            ep = f"{name}.{p.pin}"
            xy = pre_positions.get(ep)
            if xy is None:
                continue
            pad_points.append((p.pin, round(float(xy[0]), 4), round(float(xy[1]), 4)))
        if not pad_points:
            continue
        pads = [
            {"pin": pin, "x_mm": x_mm, "y_mm": y_mm} for pin, x_mm, y_mm in pad_points
        ]
        anchor_pin = (
            comp.uv_meta.anchor_pin if comp.uv_meta is not None else pads[0]["pin"]
        )
        anchor = next((p for p in pads if p["pin"] == anchor_pin), pads[0])
        rotation_deg = 0.0
        if len(pad_points) >= 2:
            dx = pad_points[1][1] - pad_points[0][1]
            dy = pad_points[1][2] - pad_points[0][2]
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                rotation_deg = round(math.degrees(math.atan2(dy, dx)), 4)
        pre_uv_placements.append(
            {
                "ref": name,
                "anchor_x_mm": anchor["x_mm"],
                "anchor_y_mm": anchor["y_mm"],
                "rotation_deg": rotation_deg,
                "pads": pads,
            }
        )

    # --- phaseA / phaseB: skeleton routes ---
    skeleton = result.phase_a.skeleton
    skeleton_routes = _collect_skeleton_routes(skeleton, result.artifact)

    # --- phaseB: UV placements ---
    uv_placements = [
        {
            "ref": name,
            "anchor_x_mm": round(float(p.anchor.x), 4),
            "anchor_y_mm": round(float(p.anchor.y), 4),
            "rotation_deg": float(p.rotation_deg),
            "pads": [
                {
                    "pin": pp.pin,
                    "x_mm": round(float(pp.point.x), 4),
                    "y_mm": round(float(pp.point.y), 4),
                }
                for pp in p.pads
            ],
        }
        for name, p in result.phase_b.adhesion.placements.items()
    ]

    # --- phaseC: geometry routes (mm) + flex routes ---
    flex_set = set(result.phase_c.routed_flex_edges)
    geo_routes = []
    flex_routes = []
    for edge_id, rp in result.geometry.routes.items():
        entry = {
            "edge_id": edge_id,
            "polyline_mm": [[round(p.x, 4), round(p.y, 4)] for p in rp.points],
            "width_mm": round(float(rp.width), 4),
            "routing_class": rp.routing_class,
            "success": True,
        }
        if edge_id in flex_set:
            flex_routes.append(entry)
        else:
            geo_routes.append(entry)
    # Failed flex edges (no polyline)
    for eid in result.phase_c.failed_flex_edges:
        flex_routes.append(
            {
                "edge_id": eid,
                "polyline_mm": [],
                "width_mm": 0.0,
                "routing_class": result.artifact.edges[eid].routing_class,
                "success": False,
            }
        )

    # --- Assemble bundle ---
    bundle = {
        "project": project,
        "board": {
            "width_mm": round(float(board.width), 4),
            "height_mm": round(float(board.height), 4),
            "origin_x_mm": round(float(board.origin.x), 4),
            "origin_y_mm": round(float(board.origin.y), 4),
        },
        "components": _make_viewer_components(result.artifact),
        "phases": {
            "preA": {"edges": prea_edges, "uv_placements": pre_uv_placements},
            "phaseA": {"routes": skeleton_routes},
            "phaseB": {"routes": skeleton_routes, "uv_placements": uv_placements},
            "phaseC": {
                "routes": geo_routes,
                "uv_placements": uv_placements,
                "flex_routes": flex_routes,
            },
        },
    }

    out_path = out_dir / f"{project}.viewer.json"
    out_path.write_text(json.dumps(bundle, indent=2))
    logger.info("persist viewer bundle: %s", out_path)
    return out_path


def _make_partial_result(
    artifact: FrontendArtifact,
    plan: Any,
    *,
    phase_a: PhaseAResult | None = None,
    phase_b: PhaseBResult | None = None,
    phase_c: PhaseCResult | None = None,
    skeleton: SkeletonReport | None = None,
) -> OrchestratorV2Result:
    """Build an :class:`OrchestratorV2Result` for phases that have not run yet.

    Missing phases are filled with empty stubs so all downstream rendering
    helpers work without ``None``-checks.  The resulting result carries no
    routing data for unrun phases.

    Parameters
    ----------
    artifact:
        Frontend artifact (always required).
    plan:
        Node plan from ``compile_and_plan`` (always required).
    phase_a, phase_b, phase_c:
        Per-phase result objects.  Pass *None* for phases not yet executed.
    skeleton:
        Post-Phase-B-retry skeleton.  Falls back to ``phase_a.skeleton`` if
        not supplied and ``phase_a`` is available, otherwise an empty skeleton.
    """
    from solver.v2.node_planner import NodePlan

    _plan: NodePlan = plan  # type: ignore[assignment]

    empty_skeleton = SkeletonReport()
    _skeleton: SkeletonReport
    if skeleton is not None:
        _skeleton = skeleton
    elif phase_a is not None:
        _skeleton = phase_a.skeleton
    else:
        _skeleton = empty_skeleton

    _phase_a = phase_a or PhaseAResult(
        skeleton=empty_skeleton, plan=_plan, wall_seconds=0.0
    )
    _phase_b = phase_b or PhaseBResult(adhesion=UvAdhesionReport(), wall_seconds=0.0)
    _phase_c = phase_c or PhaseCResult(
        routed_flex_edges=[], failed_flex_edges=[], wall_seconds=0.0
    )

    geometry = _assemble_geometry(
        artifact=artifact,
        plan=_plan,
        skeleton=_skeleton,
        adhesion=_phase_b.adhesion,
        wall_seconds=_phase_a.wall_seconds + _phase_b.wall_seconds,
    )
    return OrchestratorV2Result(
        artifact=artifact,  # type: ignore[arg-type]
        phase_a=PhaseAResult(
            skeleton=_skeleton, plan=_plan, wall_seconds=_phase_a.wall_seconds
        ),
        phase_b=_phase_b,
        phase_c=_phase_c,
        geometry=geometry,
    )


def _phase_banner(phase: str, description: str, color: str) -> str:
    """Return an SVG <text> element showing the phase label near the top."""
    return (
        f'<text x="6" y="26" font-family="sans-serif" font-size="9" '
        f'font-weight="bold" fill="{color}">'
        f"[{escape(phase)}] {escape(description)}</text>"
    )


def _phase_b_rlc_overlay(
    geom: GeometryIR,
    result: OrchestratorV2Result,
    layout: V33Layout | None,
    *,
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    """Render placed UV/RLC components with bbox + pads + GND markers (WI-D5)."""
    if layout is None:
        return ""
    board_h = float(geom.board.height) + 2 * margin_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h - (mm + margin_mm)) * px_per_mm

    parts: list[str] = [
        "<style>",
        ".prea-rlc-bbox{fill:#ecfdf5;stroke:#0f766e;stroke-width:0.8;"
        "stroke-dasharray:2 2;opacity:0.85;}",
        ".prea-rlc-pad{fill:#cbd5e1;stroke:#475569;stroke-width:0.7;opacity:0.95;}",
        ".prea-gnd-pin{fill:#f59e0b;stroke:#92400e;stroke-width:0.7;}",
        ".phb-name-label{fill:#0f172a;font-family:Arial,sans-serif;font-size:7px;"
        "font-weight:bold;}",
        ".phb-net-label{fill:#7c2d12;font-family:Arial,sans-serif;font-size:6px;"
        "font-weight:bold;}",
        ".phb-pin-label{fill:#111827;font-family:Arial,sans-serif;font-size:5px;"
        "font-weight:bold;stroke:#ffffff;stroke-width:0.6;paint-order:stroke;}",
        "</style>",
        '<g class="phaseB-rlc">',
    ]

    def _emit_polygon(cls: str, pts: list[tuple[float, float]], title: str) -> str:
        coords = " ".join(f"{_x(px):.2f},{_y(py):.2f}" for px, py in pts)
        return f'<polygon class="{cls}" points="{coords}"><title>{escape(title)}</title></polygon>'

    for comp_id, uv in sorted(result.artifact.uv_components.items()):
        placement = geom.placements.get(comp_id)
        if placement is None:
            continue
        component = layout.components.get(comp_id)
        if component is None or component.footprint_ref is None:
            continue
        footprint = layout.footprints.get(component.footprint_ref)
        if footprint is None:
            continue
        ax = float(placement.anchor.x)
        ay = float(placement.anchor.y)
        rot = float(placement.rotation_deg or 0.0)
        cos_t = math.cos(math.radians(rot))
        sin_t = math.sin(math.radians(rot))

        def _world(
            lx: float,
            ly: float,
            ax: float = ax,
            ay: float = ay,
            cos_t: float = cos_t,
            sin_t: float = sin_t,
        ) -> tuple[float, float]:
            return (ax + cos_t * lx - sin_t * ly, ay + sin_t * lx + cos_t * ly)

        bbox = uv.bbox
        if bbox is not None:
            local_corners = [
                (bbox.min_x, bbox.min_y),
                (bbox.max_x, bbox.min_y),
                (bbox.max_x, bbox.max_y),
                (bbox.min_x, bbox.max_y),
            ]
            pts = [_world(lx, ly) for lx, ly in local_corners]
            parts.append(_emit_polygon("prea-rlc-bbox", pts, comp_id))

        gnd_pins = {
            pin_name
            for pin_name, net_name in component.pin_nets.items()
            if net_name.strip().upper() == "GND"
        }

        for pad in placement.pads:
            pin = footprint.pins.get(pad.pin)
            geom_pad = pin.pad_geometry if pin is not None else None
            if (
                geom_pad is None
                or geom_pad.length is None
                or geom_pad.width is None
                or (geom_pad.shape or "rect").lower() != "rect"
            ):
                continue
            pcx = float(pad.point.x)
            pcy = float(pad.point.y)
            pad_rot = rot + float(pin.local_orientation or 0.0 if pin else 0.0)
            cp = math.cos(math.radians(pad_rot))
            sp = math.sin(math.radians(pad_rot))
            hl = float(geom_pad.length) / 2.0
            hw = float(geom_pad.width) / 2.0
            pad_pts = [
                (pcx + cp * lx - sp * ly, pcy + sp * lx + cp * ly)
                for lx, ly in ((-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw))
            ]
            cls = "prea-gnd-pin" if pad.pin in gnd_pins else "prea-rlc-pad"
            parts.append(_emit_polygon(cls, pad_pts, f"{comp_id}.{pad.pin}"))
            # WI-I7: RLC pin labels removed per user request
            # (only show component name, not individual pin numbers like C2.1, C2.2)
            if pad.pin in gnd_pins:
                parts.append(
                    f'<text class="phb-net-label" x="{_x(pcx)+3:.2f}" '
                    f'y="{_y(pcy)+8:.2f}">GND</text>'
                )

        label = _prea_short_name(comp_id)
        if bbox is not None:
            # anchor label at upper-left corner of rotated bbox
            lcx = (bbox.min_x + bbox.max_x) / 2.0
            lcy = bbox.max_y
            wx, wy = _world(lcx, lcy)
            parts.append(
                f'<text class="phb-name-label" x="{_x(wx):.2f}" '
                f'y="{_y(wy)-2:.2f}" text-anchor="middle">{escape(label)}</text>'
            )

    parts.append("</g>")
    return "".join(parts)


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


def _phase_c_overlay(
    geom: GeometryIR,
    result: OrchestratorV2Result,
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    """WI-J6 phaseC overlay.

    * Floating-component bbox 用蓝色描边 + 名称标签
    * Flexible routes 用紫色加粗线
    * 失败的 flex 边在端点画红色虚线 X 标记
    """
    board_h = float(geom.board.height) + 2 * margin_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h - (mm + margin_mm)) * px_per_mm

    floating_names = {
        n
        for n, c in result.artifact.components.items()
        if c.placement_kind == "floating"
    }
    flex_routed = set(result.phase_c.routed_flex_edges)
    flex_failed = set(result.phase_c.failed_flex_edges)

    parts: list[str] = ['<g class="phaseC">']

    # 1) Floating component bboxes (blue)
    for name in floating_names:
        placement = geom.placements.get(name)
        if placement is None or not placement.pads:
            continue
        xs = [float(p.point.x) for p in placement.pads]
        ys = [float(p.point.y) for p in placement.pads]
        bx, by = min(xs) - 0.4, min(ys) - 0.4
        w = (max(xs) - min(xs)) + 0.8
        h = (max(ys) - min(ys)) + 0.8
        parts.append(
            f'<rect x="{_x(bx):.2f}" y="{_y(by + h):.2f}" '
            f'width="{w * px_per_mm:.2f}" height="{h * px_per_mm:.2f}" '
            'fill="#dbeafe" stroke="#1d4ed8" stroke-width="1.5" '
            'opacity="0.85"/>'
        )
        parts.append(
            f'<text x="{_x(bx):.2f}" y="{_y(by + h) - 2:.2f}" '
            'font-family="sans-serif" font-size="8" '
            'fill="#1d4ed8" font-weight="bold">'
            f"{escape(name)}</text>"
        )
        for pad in placement.pads:
            parts.append(
                f'<circle cx="{_x(float(pad.point.x)):.2f}" '
                f'cy="{_y(float(pad.point.y)):.2f}" r="2.2" '
                'fill="#1d4ed8" opacity="0.9"/>'
            )

    # 2) Flex routes (purple thick)
    for eid in flex_routed:
        route = geom.routes.get(eid)
        if route is None or len(route.points) < 2:
            continue
        pts = " ".join(
            f"{_x(float(p.x)):.2f},{_y(float(p.y)):.2f}" for p in route.points
        )
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="#7c3aed" '
            'stroke-width="2.2" stroke-linecap="round" '
            'stroke-linejoin="round" opacity="0.95"/>'
        )

    # 3) Failed flex edges — red dashed X markers at endpoints
    for eid in flex_failed:
        edge = result.artifact.edges.get(eid)
        if edge is None or len(edge.connections) < 2:
            continue
        # Try to recover endpoint positions from placements
        pad_xy: dict[str, tuple[float, float]] = {}
        for cname, placement in geom.placements.items():
            for pad in placement.pads:
                pad_xy[f"{cname}.{pad.pin}"] = (
                    float(pad.point.x),
                    float(pad.point.y),
                )
        for ep in (edge.connections[0], edge.connections[-1]):
            xy = pad_xy.get(ep)
            if xy is None:
                continue
            cx, cy = _x(xy[0]), _y(xy[1])
            parts.append(
                f'<g stroke="#dc2626" stroke-width="2" stroke-dasharray="3,2">'
                f'<line x1="{cx - 5:.2f}" y1="{cy - 5:.2f}" '
                f'x2="{cx + 5:.2f}" y2="{cy + 5:.2f}"/>'
                f'<line x1="{cx - 5:.2f}" y1="{cy + 5:.2f}" '
                f'x2="{cx + 5:.2f}" y2="{cy - 5:.2f}"/></g>'
            )

    parts.append("</g>")
    return "".join(parts)


def _pin_label_text(pin: str) -> str:
    if pin.startswith("P") and pin[1:].isdigit():
        return f"PIN_{pin[1:]}"
    if pin.startswith("PIN_"):
        return pin
    return pin


def _pin_label_overlay(
    geom: GeometryIR,
    *,
    skip_components: frozenset[str] = frozenset(),
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    """SVG overlay to show per-pad pin numbers from GeometryIR placements.

    WI-F2: ``skip_components`` lets callers exclude UV/RLC components whose
    pad labels are rendered by ``_phase_b_rlc_overlay`` on top of the bbox /
    pad polygons (otherwise the bbox covers these labels).

    WI-I7: Skip RLC components (C*, R*, L*) - only show PIN labels for IC/connector.
    """
    board_h = float(geom.board.height) + 2 * margin_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h - (mm + margin_mm)) * px_per_mm

    parts: list[str] = []
    for comp_name, placement in geom.placements.items():
        if comp_name in skip_components:
            continue
        # WI-I7: Skip RLC components - only show PIN labels for IC/connector
        if comp_name and comp_name[0] in ("C", "R", "L"):
            continue
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


def _phase_a_diag_overlay(
    result: OrchestratorV2Result,
    geom: GeometryIR,
    px_per_mm: float = 6.0,
    margin_mm: float = 5.0,
) -> str:
    """Diagnostic overlay for Phase A: length errors, segment crossings,
    and failed edge markers. Helps the user spot WI-A4/A5 issues at a glance.
    """
    board_h = float(geom.board.height) + 2 * margin_mm

    def _x(mm: float) -> float:
        return (mm + margin_mm) * px_per_mm

    def _y(mm: float) -> float:
        return (board_h - (mm + margin_mm)) * px_per_mm

    parts: list[str] = []
    routes = result.phase_a.skeleton.routes

    # Node-id labels (preA-style): place a small label near every endpoint
    # used by a successful route so the user can correlate route ids with
    # node ids. Deduplicate by world position to avoid stacking when
    # several edges share a junction.
    ep_xy_map = result.phase_a.plan.endpoint_xy
    drawn_node_positions: set[tuple[float, float]] = set()
    for edge_id, route in routes.items():
        if not route.success:
            continue
        edge = result.artifact.edges.get(edge_id)
        if edge is None:
            continue
        for node_id in edge.connections:
            xy = ep_xy_map.get(node_id)
            if xy is None:
                continue
            key = (round(xy[0], 2), round(xy[1], 2))
            if key in drawn_node_positions:
                continue
            # WI-I7: Skip RLC PIN labels (e.g., C2.1, R3.2) - only show IC/connector endpoints
            if "." in node_id:
                comp_id = node_id.split(".")[0]
                if comp_id and comp_id[0] in ("C", "R", "L"):
                    continue
            drawn_node_positions.add(key)
            label = _prea_short_name(node_id)
            parts.append(
                f'<text x="{_x(xy[0]):.2f}" y="{_y(xy[1]) - 4:.2f}" '
                f'font-family="sans-serif" font-size="4.5" fill="#7c3aed" '
                'text-anchor="middle" stroke="#ffffff" stroke-width="0.5" '
                'paint-order="stroke">'
                f"{escape(label)}</text>"
            )

    # preA-style: visible label is just the short edge id; detailed
    # width/length/err information is exposed via <title> tooltips on
    # transparent overlay lines that lie on the route midpoint.
    for edge_id, route in routes.items():
        if not route.success or not route.polyline_um:
            continue
        mid = route.polyline_um[len(route.polyline_um) // 2]
        mx, my = mid[0] / 1000.0, mid[1] / 1000.0
        short = _prea_short_name(edge_id)
        edge = result.artifact.edges.get(edge_id)
        width_mm = float(edge.width) if (edge and edge.width is not None) else 0.0
        if route.target_mm is not None:
            err = (route.length_mm - route.target_mm) / route.target_mm * 100.0
            tooltip = (
                f"{edge_id} | w={width_mm:.2f}mm | "
                f"L={route.length_mm:.2f}/{route.target_mm:.2f}mm "
                f"({err:+.1f}%)"
            )
        else:
            tooltip = f"{edge_id} | w={width_mm:.2f}mm | L={route.length_mm:.2f}mm"
        # Invisible overlay marker carries the tooltip so the user can hover
        # the label region for the full diagnostic.
        parts.append(
            f'<circle cx="{_x(mx):.2f}" cy="{_y(my):.2f}" r="3" '
            f'fill="rgba(0,0,0,0)"><title>{escape(tooltip)}</title></circle>'
        )
        parts.append(
            f'<text x="{_x(mx):.2f}" y="{_y(my) + 7:.2f}" '
            f'font-family="sans-serif" font-size="5.5" fill="#1f2937" '
            'text-anchor="middle" stroke="#ffffff" stroke-width="0.6" paint-order="stroke">'
            f"{escape(short)}</text>"
        )

    # Failed edges marker (red dashed line between endpoints).
    for edge_id, route in routes.items():
        if route.success:
            continue
        edge = result.artifact.edges.get(edge_id)
        if edge is None:
            continue
        ep_xy = result.phase_a.plan.endpoint_xy
        s = ep_xy.get(edge.connections[0])
        g = ep_xy.get(edge.connections[1])
        if s is None or g is None:
            continue
        parts.append(
            f'<line x1="{_x(s[0]):.2f}" y1="{_y(s[1]):.2f}" '
            f'x2="{_x(g[0]):.2f}" y2="{_y(g[1]):.2f}" '
            'stroke="#dc2626" stroke-width="1.2" stroke-dasharray="3,2" />'
        )

    # Segment crossings (CCW intersection between different edges).
    def _ccw(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def _seg_inter(a1, a2, b1, b2):
        # Exact endpoint coincidence (junction shared) -> not a crossing.
        if a1 == b1 or a1 == b2 or a2 == b1 or a2 == b2:
            return None
        # Tolerant near-coincidence (chamfered tail touches another chamfer).
        eps = 250.0  # μm
        for ea in (a1, a2):
            for eb in (b1, b2):
                if abs(ea[0] - eb[0]) < eps and abs(ea[1] - eb[1]) < eps:
                    return None
        d1 = _ccw(b1, b2, a1)
        d2 = _ccw(b1, b2, a2)
        d3 = _ccw(a1, a2, b1)
        d4 = _ccw(a1, a2, b2)
        if (d1 * d2 < 0) and (d3 * d4 < 0):
            denom = (a2[0] - a1[0]) * (b2[1] - b1[1]) - (a2[1] - a1[1]) * (
                b2[0] - b1[0]
            )
            if denom == 0:
                return None
            t = (
                (b1[0] - a1[0]) * (b2[1] - b1[1]) - (b1[1] - a1[1]) * (b2[0] - b1[0])
            ) / denom
            return (a1[0] + t * (a2[0] - a1[0]), a1[1] + t * (a2[1] - a1[1]))
        return None

    segs: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
    for edge_id, route in routes.items():
        if not route.success or len(route.polyline_um) < 2:
            continue
        for a, b in zip(route.polyline_um, route.polyline_um[1:]):
            segs.append((edge_id, a, b))
    seen: set[tuple[float, float]] = set()
    for i in range(len(segs)):
        eid_a, a1, a2 = segs[i]
        for j in range(i + 1, len(segs)):
            eid_b, b1, b2 = segs[j]
            if eid_a == eid_b:
                continue
            pt = _seg_inter(a1, a2, b1, b2)
            if pt is None:
                continue
            cx, cy = pt[0] / 1000.0, pt[1] / 1000.0
            key = (round(cx, 2), round(cy, 2))
            if key in seen:
                continue
            seen.add(key)
            r = 4.5
            parts.append(
                f'<circle cx="{_x(cx):.2f}" cy="{_y(cy):.2f}" r="{r}" '
                'fill="none" stroke="#dc2626" stroke-width="1.5" />'
                f'<line x1="{_x(cx) - r:.2f}" y1="{_y(cy) - r:.2f}" '
                f'x2="{_x(cx) + r:.2f}" y2="{_y(cy) + r:.2f}" '
                'stroke="#dc2626" stroke-width="1.5" />'
                f'<line x1="{_x(cx) - r:.2f}" y1="{_y(cy) + r:.2f}" '
                f'x2="{_x(cx) + r:.2f}" y2="{_y(cy) - r:.2f}" '
                'stroke="#dc2626" stroke-width="1.5" />'
            )

    return "".join(parts)


def _render_svg(
    geom: GeometryIR,
    path: Path,
    *,
    banner: str = "",
    overlay: str = "",
    layout: V33Layout | None = None,
    show_pads: bool = True,
    show_pad_labels: bool = True,  # WI-I7: control PIN label rendering
) -> None:
    """Write a rendered SVG to *path*, injecting an optional phase banner and overlay."""
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = render_full_layout(
        geom, layout=layout, show_pads=show_pads, show_pad_labels=show_pad_labels
    )
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
    zero_length_edges: set[str] = set()

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
                    if seg_len <= 1e-9:
                        zero_length_edges.add(edge_id)
                    else:
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
    if zero_length_edges:
        logger.warning(
            "preA skipped target projection for zero-length edges: %s",
            ", ".join(sorted(zero_length_edges)),
        )
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
    # WI-F1a: delegate to shared short_id implementation (generic across
    # components, used both by base geom_svg renderer and overlay code).
    from postproc.short_name import short_id

    return short_id(name)


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
    _ = branch_offset_u_tokens
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
                theta = math.radians(float(branch.angle_deg))
                cos_t = math.cos(theta)
                sin_t = math.sin(theta)
                bdx = ux * cos_t - uy * sin_t
                bdy = ux * sin_t + uy * cos_t
                branch_edge = artifact.edges.get(branch.edge_id)
                anchor = _clamp_board(
                    (
                        nx
                        + float(branch.offset_u) * bdx
                        + float(branch.signed_v) * (-bdy),
                        ny
                        + float(branch.offset_u) * bdy
                        + float(branch.signed_v) * bdx,
                    ),
                    board_w,
                    board_h,
                )
                branch_len = 1.0
                if branch_edge is not None and branch_edge.target_length is not None:
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
                edge_endpoint_overrides.setdefault(branch.edge_id, {})[node_id] = anchor
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


def solve_pre_a_from_artifact(
    artifact: FrontendArtifact,
    plan_xy: dict[str, tuple[float, float]],
    *,
    board_w: float,
    board_h: float,
    junction_templates: dict[str, UniversalJunctionTemplate] | None = None,
    branch_offset_u_tokens: dict[str, str] | None = None,
) -> tuple[dict[str, tuple[float, float]], dict[str, dict[str, tuple[float, float]]]]:
    """Public PreA solver: relax endpoint positions to satisfy target_length.

    Identical pipeline to :func:`_solve_pre_a_positions` but does not require
    a fully constructed :class:`OrchestratorV2Result`. Intended to be called
    BEFORE Phase A so the router can consume preA-refined endpoints.
    """

    class _Stub:
        def __init__(self) -> None:
            class _PA:
                plan: Any = None

            self.artifact = artifact
            self.phase_a = _PA()
            self.phase_a.plan = type("P", (), {"endpoint_xy": dict(plan_xy)})()

    return _solve_pre_a_positions(
        _Stub(),  # type: ignore[arg-type]
        board_w=board_w,
        board_h=board_h,
        junction_templates=junction_templates,
        branch_offset_u_tokens=branch_offset_u_tokens,
    )


def _solve_pre_a_positions(
    result: OrchestratorV2Result,
    *,
    board_w: float,
    board_h: float,
    junction_templates: dict[str, UniversalJunctionTemplate] | None = None,
    branch_offset_u_tokens: dict[str, str] | None = None,
) -> tuple[dict[str, tuple[float, float]], dict[str, dict[str, tuple[float, float]]]]:
    """Relax endpoint positions to satisfy target-length proportions before routing.

    若环境变量 ``PREA_PIPELINE=legacy`` 则走旧版（保留作为安全回退）；
    否则默认使用 :mod:`solver.v2.prea_pipeline` 的场景化新流水线.
    """
    import os

    artifact = result.artifact
    plan_xy = dict(result.phase_a.plan.endpoint_xy)

    if os.environ.get("PREA_PIPELINE", "scene_split").lower() == "scene_split":
        from solver.v2.prea_pipeline import solve_prea_scene_split

        positions, edge_endpoint_overrides = solve_prea_scene_split(
            artifact,
            plan_xy,
            board_w=board_w,
            board_h=board_h,
            junction_templates=junction_templates,
            branch_offset_u_tokens=branch_offset_u_tokens,
            seed_fixed_positions=_seed_fixed_positions,
            apply_junction_templates=_apply_junction_templates,
            propagate_constrained_edges=_propagate_constrained_edges,
        )
        # 场景 2 端点 fallback：未输出位置的 UV pin 沿用 plan_xy.
        for ep, xy in plan_xy.items():
            positions.setdefault(ep, (float(xy[0]), float(xy[1])))
        return positions, edge_endpoint_overrides

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
    t_all = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    project = result.geometry.project
    artefacts: dict[str, Path] = {}

    t_step = time.perf_counter()
    summary = phase_summary(result)
    summary_path = out_dir / f"{project}.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    artefacts["summary"] = summary_path
    logger.info("persist summary: wall=%.2fs", time.perf_counter() - t_step)

    # Pre-Phase-A: raw YAML topology connectivity snapshot.
    t_step = time.perf_counter()
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
    logger.info("persist preA svg: wall=%.2fs", time.perf_counter() - t_step)

    t_step = time.perf_counter()
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
    logger.info("persist preA json: wall=%.2fs", time.perf_counter() - t_step)

    # Per-phase JSON (Phase A and B are the meaningful ones).
    t_step = time.perf_counter()
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
    logger.info("persist phaseA json: wall=%.2fs", time.perf_counter() - t_step)

    t_step = time.perf_counter()
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
    logger.info("persist phaseB json: wall=%.2fs", time.perf_counter() - t_step)

    # Per-phase SVG snapshots
    routed_ok, routed_total = result.phase_a.skeleton.success_rate()
    uv_names = set(result.phase_b.adhesion.placements.keys())
    uv_placed = len(uv_names)
    uv_total = len(result.artifact.uv_components)
    flex_ok = len(result.phase_c.routed_flex_edges)
    flex_failed = len(result.phase_c.failed_flex_edges)

    phase_a_svg = out_dir / f"{project}.phaseA.svg"
    phase_a_geom = _build_phase_a_geom(result)
    t_step = time.perf_counter()
    _render_svg(
        phase_a_geom,
        phase_a_svg,
        banner=_phase_banner(
            "Phase A",
            f"Skeleton routing — {routed_ok}/{routed_total} microstrips routed | UV not yet placed",
            "#b45309",
        ),
        overlay=_pin_label_overlay(phase_a_geom)
        + _phase_a_diag_overlay(result, phase_a_geom),
        layout=layout,
    )
    artefacts["phaseA_svg"] = phase_a_svg
    logger.info("persist phaseA svg: wall=%.2fs", time.perf_counter() - t_step)

    geom_b = _build_phase_b_geom(result)
    phase_b_svg = out_dir / f"{project}.phaseB.svg"
    t_step = time.perf_counter()
    _render_svg(
        geom_b,
        phase_b_svg,
        banner=_phase_banner(
            "Phase B",
            f"UV adhesion — {uv_placed}/{uv_total} components placed (highlighted green)",
            "#15803d",
        ),
        overlay=_pin_label_overlay(
            geom_b,
            skip_components=frozenset(result.artifact.uv_components.keys()),
        )
        + _phase_a_diag_overlay(result, geom_b)
        + _phase_b_rlc_overlay(geom_b, result, layout),
        layout=layout,
        show_pad_labels=False,  # WI-I7: disable pad labels (use _pin_label_overlay instead)
    )
    artefacts["phaseB_svg"] = phase_b_svg
    logger.info("persist phaseB svg: wall=%.2fs", time.perf_counter() - t_step)

    # Phase C SVG = final geometry (flex routes already in result.geometry)
    phase_c_svg = out_dir / f"{project}.phaseC.svg"
    t_step = time.perf_counter()
    _render_svg(
        result.geometry,
        phase_c_svg,
        banner=_phase_banner(
            "Phase C",
            f"Flex routing — {flex_ok} routed / {flex_failed} failed",
            "#1d4ed8",
        ),
        overlay=_pin_label_overlay(
            result.geometry,
            skip_components=frozenset(result.artifact.uv_components.keys()),
        )
        + _phase_b_rlc_overlay(result.geometry, result, layout)
        + _phase_c_overlay(result.geometry, result),
        layout=layout,
        show_pad_labels=False,
    )
    artefacts["phaseC_svg"] = phase_c_svg
    logger.info("persist phaseC svg: wall=%.2fs", time.perf_counter() - t_step)
    logger.info("persist artefacts total: wall=%.2fs", time.perf_counter() - t_all)

    t_step = time.perf_counter()
    viewer_path = _emit_viewer_bundle(result, out_dir, layout=layout)
    artefacts["viewer_bundle"] = viewer_path
    logger.info("persist viewer bundle: wall=%.2fs", time.perf_counter() - t_step)

    return artefacts


def _emit_svg(
    result: OrchestratorV2Result, svg_path: Path, *, layout: V33Layout | None = None
) -> None:
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_text = render_full_layout(result.geometry, layout=layout)
    svg_path.write_text(svg_text)


def solve_and_emit(
    yaml_path: Path,
    out_dir: Path,
) -> dict[str, Path]:
    """Test/automation entrypoint: solve a YAML layout and persist phase artefacts."""
    layout = load_v33_layout(yaml_path)
    result = solve_layout_v2(yaml_path)
    return _persist_phase_artefacts(
        result, out_dir, layout_path=yaml_path, layout=layout
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )
    logger.info(
        "pcb_solve_v2 start: layout=%s stop_after=%s", args.layout, args.stop_after
    )

    t_step = time.perf_counter()
    options = OrchestratorV2Options(
        clearance_mm=args.clearance_mm,
        grid_step_um=args.grid_step_um,
        rip_up_rounds=args.rip_up_rounds,
        out_dir=args.out_dir,
    )
    logger.info("build options: wall=%.2fs", time.perf_counter() - t_step)

    layout = load_v33_layout(args.layout)

    stop_after = args.stop_after

    # ---- Pre-A: compile + plan node positions --------------------------------
    t_step = time.perf_counter()
    artifact, plan = compile_and_plan(args.layout, options=options)
    logger.info("compile_and_plan: wall=%.2fs", time.perf_counter() - t_step)

    if stop_after == "preA":
        result = _make_partial_result(artifact, plan)
        artefacts = _persist_phase_artefacts(
            result, args.out_dir, layout_path=Path(args.layout), layout=layout
        )
        _print_summary(result, artefacts, args, stop_after)
        return 0

    # ---- Phase A: skeleton routing -------------------------------------------
    t_step = time.perf_counter()
    phase_a = run_phase_a(artifact, plan, args.layout, options=options)
    logger.info("run_phase_a: wall=%.2fs", time.perf_counter() - t_step)

    if stop_after == "phaseA":
        result = _make_partial_result(artifact, plan, phase_a=phase_a)
        artefacts = _persist_phase_artefacts(
            result, args.out_dir, layout_path=Path(args.layout), layout=layout
        )
        _print_summary(result, artefacts, args, stop_after)
        return 0 if phase_a.skeleton.success_rate()[0] > 0 else 2

    # ---- Phase B: UV adhesion + retry ----------------------------------------
    t_step = time.perf_counter()
    phase_b, skeleton = run_phase_b(artifact, plan, phase_a, options=options)
    logger.info("run_phase_b: wall=%.2fs", time.perf_counter() - t_step)

    if stop_after == "phaseB":
        result = _make_partial_result(
            artifact, plan, phase_a=phase_a, phase_b=phase_b, skeleton=skeleton
        )
        artefacts = _persist_phase_artefacts(
            result, args.out_dir, layout_path=Path(args.layout), layout=layout
        )
        _print_summary(result, artefacts, args, stop_after)
        return 0 if skeleton.success_rate()[0] > 0 else 2

    # ---- Phase C: floating placement + flex routing -------------------------
    t_step = time.perf_counter()
    phase_c, geometry = run_phase_c(
        args.layout, artifact, plan, skeleton, phase_a, phase_b, options=options
    )
    logger.info("run_phase_c: wall=%.2fs", time.perf_counter() - t_step)

    result = OrchestratorV2Result(
        artifact=artifact,
        phase_a=PhaseAResult(
            skeleton=skeleton, plan=plan, wall_seconds=phase_a.wall_seconds
        ),
        phase_b=phase_b,
        phase_c=phase_c,
        geometry=geometry,
    )

    t_step = time.perf_counter()
    artefacts = _persist_phase_artefacts(
        result, args.out_dir, layout_path=Path(args.layout), layout=layout
    )
    logger.info("persist phase artefacts: wall=%.2fs", time.perf_counter() - t_step)

    svg_path = args.svg_out or (args.out_dir / f"{result.geometry.project}.final.svg")
    t_step = time.perf_counter()
    _emit_svg(result, svg_path, layout=layout)
    logger.info(
        "persist final svg: wall=%.2fs path=%s", time.perf_counter() - t_step, svg_path
    )
    artefacts["final_svg"] = svg_path

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(phase_summary(result), indent=2, default=str)
        )

    _print_summary(result, artefacts, args, stop_after)
    routed_ok, _ = result.phase_a.skeleton.success_rate()
    return 0 if routed_ok > 0 else 2


def _print_summary(
    result: OrchestratorV2Result,
    artefacts: dict[str, Path],
    args: argparse.Namespace,
    stop_after: str,
) -> None:
    """Print per-phase statistics to stdout unless --quiet."""
    if args.quiet:
        return
    routed_ok, routed_total = result.phase_a.skeleton.success_rate()
    uv_placed = len(result.phase_b.adhesion.placements)
    uv_total = len(result.artifact.uv_components)
    wall_total = result.geometry.solve_wall_seconds
    print(
        f"[v2] project={result.geometry.project} "
        f"stop_after={stop_after} "
        f"status={result.geometry.solve_status} "
        f"phase_a={routed_ok}/{routed_total} "
        f"uv={uv_placed}/{uv_total} "
        f"wall={wall_total:.2f}s"
    )
    for name, path in artefacts.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    sys.exit(main())
