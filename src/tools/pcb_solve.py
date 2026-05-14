"""``pcb_solve`` CLI — single-command end-to-end PCB layout pipeline (M5).

Wraps :func:`solver.orchestrator.solve_layout` so users can drive the full
``YAML → SolverIR → SA seed → CP-SAT → A* → audit → SVG/JSON`` flow with a
single command. Inherits CLI shape and exit codes from M4 ``cpsat_solve``.

Usage::

    pcb_solve rf_layout_simplified.yaml \
        --time-limit 10 --workers 8 \
        --svg-out out/PA_Module_Simplified.geom.svg \
        --report-out out/PA_Module_Simplified.geom.json

Flags
-----

* ``--no-sa``      Disable Phase 1 SA seeding (cold-start CP-SAT, M4 behaviour).
* ``--no-astar``   Disable Phase 3 A* (flexible_path edges keep direct polylines).
* ``--max-retries`` Number of escalation attempts on INFEASIBLE (default 5).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from postproc import (
    analyze_crossings,
    apply_bends,
    apply_meanders,
    render_geometry_svg,
    run_drc,
    run_lvs,
)
from postproc.crossing_report_io import to_json as crossing_to_json
from postproc.crossing_report_io import to_markdown as crossing_to_md
from schema.v33 import load_v33_layout
from solver import (
    DEFAULT_GRID_STEP_UM,
    DEFAULT_NUM_WORKERS,
    DEFAULT_TIME_LIMIT_S,
    OrchestratorOptions,
    RouteOrchestratorConfig,
    solve_layout,
)
from output import render_full_layout


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcb_solve",
        description=(
            "Run the M5 three-phase pipeline (SA seed → CP-SAT → A*) on an "
            "rf_microwave_layout v3.3 YAML and emit GeometryIR + SVG/JSON."
        ),
    )
    p.add_argument("layout", type=Path, help="Path to v3.3 YAML.")
    p.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT_S,
        help="CP-SAT wall-time budget per attempt (seconds). Default 10.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_NUM_WORKERS,
        help="num_search_workers. Default 8.",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="INFEASIBLE retry budget with hotter SA + longer time. Default 5.",
    )
    p.add_argument(
        "--no-sa",
        action="store_true",
        help="Skip Phase 1 SA seeding (cold-start CP-SAT).",
    )
    p.add_argument(
        "--no-astar",
        action="store_true",
        help="Skip Phase 3 A* routing of flexible_path edges.",
    )
    p.add_argument(
        "--svg-out",
        type=Path,
        default=None,
        help="Optional SVG output path.",
    )
    p.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Optional JSON report path.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-edge length error printout.",
    )
    p.add_argument(
        "--bend",
        dest="bend",
        action="store_true",
        default=True,
        help="Render bend_style geometry (mitered/curved) before DRC/SVG. Default ON (M6).",
    )
    p.add_argument(
        "--no-bend",
        dest="bend",
        action="store_false",
        help="Skip bend rendering — emit raw straight-segment polylines (M5 behaviour).",
    )
    p.add_argument(
        "--meander",
        dest="meander",
        action="store_true",
        default=False,
        help="Apply U-shaped meander loops where target_length > actual routed length (M7).",
    )
    p.add_argument(
        "--no-meander",
        dest="meander",
        action="store_false",
        help="Skip meander insertion (default).",
    )
    p.add_argument(
        "--drc-out",
        type=Path,
        default=None,
        help="Optional DRC report JSON path (M6).",
    )
    p.add_argument(
        "--lvs-out",
        type=Path,
        default=None,
        help="Optional LVS report JSON path (M6).",
    )
    p.add_argument(
        "--final-svg",
        type=Path,
        default=None,
        help="Optional final SVG path (with footprint outlines + bend + DRC overlay).",
    )
    p.add_argument(
        "--crossing-report-json",
        type=Path,
        default=None,
        help="Optional M8 crossing diagnostics JSON path.",
    )
    p.add_argument(
        "--crossing-report-md",
        type=Path,
        default=None,
        help="Optional M8 crossing diagnostics Markdown path.",
    )
    p.add_argument(
        "--show-pads",
        dest="show_pads",
        action="store_true",
        default=True,
        help="Render footprint pads in --final-svg (default ON, M8).",
    )
    p.add_argument(
        "--no-show-pads",
        dest="show_pads",
        action="store_false",
        help="Skip pad rendering in --final-svg.",
    )
    p.add_argument(
        "--octilinear",
        dest="octilinear",
        action="store_true",
        default=True,
        help="Enable M9 octilinear A* router + CP-SAT length-lock relaxation (default ON).",
    )
    p.add_argument(
        "--no-octilinear",
        dest="octilinear",
        action="store_false",
        help="Disable octilinear router (M8 behaviour: CP-SAT straight-line traces only).",
    )
    p.add_argument(
        "--ripup-rounds",
        type=int,
        default=10,
        help="M9 rip-up-and-reroute round budget. Default 10.",
    )
    p.add_argument(
        "--astar-step-um",
        type=int,
        default=DEFAULT_GRID_STEP_UM,
        help=f"M9 octilinear grid step in µm. Default {DEFAULT_GRID_STEP_UM}.",
    )
    return p


def _report_dict(result) -> dict:
    geom = result.geometry
    audit = result.audit
    sa = result.sa
    return {
        "project": geom.project,
        "solve_status": geom.solve_status,
        "wall_seconds": geom.solve_wall_seconds,
        "objective": geom.objective_value,
        "attempts": result.attempts,
        "max_length_error_fraction": audit.max_length_error_fraction,
        "locked_edges_within_tolerance": audit.locked_edges_within_tolerance,
        "no_overlap_pass": audit.no_overlap_pass,
        "skipped_locked_edges": [
            {"edge_id": eid, "reason": reason}
            for eid, reason in result.skipped_locked_edges
        ],
        "sa": (
            {
                "initial_energy": sa.initial_energy,
                "final_energy": sa.final_energy,
                "accepted": sa.accepted,
                "rejected": sa.rejected,
                "iterations": sa.iterations,
                "converged": sa.converged,
                "uv_components": len(sa.placements),
            }
            if sa is not None
            else None
        ),
        "astar": {
            "routed_edges": list(result.astar.routed_edges),
            "failed_edges": list(result.astar.failed_edges),
        },
        "octilinear": (
            {
                "routed_edges": list(result.route.routed),
                "unrouted_edges": list(result.route.unrouted),
                "rounds_used": result.route.rounds_used,
                "ripup_count": dict(result.route.ripup_count),
                "overshoot_edges": list(result.route.overshoot_edges),
                "expansions_total": result.route.expansions_total,
            }
            if result.route is not None
            else None
        ),
        "length_checks": [
            {
                "edge_id": c.edge_id,
                "target_length_mm": c.target_length,
                "actual_length_mm": c.actual_length,
                "error_fraction": c.error_fraction,
            }
            for c in audit.length_checks
        ],
        "overlap_pairs": [
            {"edge_a": o.edge_a, "edge_b": o.edge_b, "overlap_um": o.overlap_um}
            for o in audit.overlap_pairs
        ],
    }


def _drc_dict(report) -> dict:
    return {
        "min_trace_width_mm": report.min_trace_width,
        "min_clearance_mm": report.min_clearance,
        "critical": report.critical_count,
        "warning": report.warning_count,
        "violations": [
            {
                "rule": v.rule,
                "severity": v.severity,
                "edge_a": v.edge_a,
                "edge_b": v.edge_b,
                "detail": v.detail,
            }
            for v in report.violations
        ],
    }


def _lvs_dict(report) -> dict:
    return {
        "skipped": report.skipped,
        "reason": report.reason,
        "checked_nets": list(report.checked_nets),
        "mismatches": [
            {
                "logical_net": m.logical_net,
                "component_count": m.component_count,
                "members": list(m.members),
            }
            for m in report.mismatches
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.layout.exists():
        print(f"layout not found: {args.layout}", file=sys.stderr)
        return 2

    options = OrchestratorOptions(
        use_sa=not args.no_sa,
        use_astar=not args.no_astar,
        use_octilinear=args.octilinear,
        time_limit_s=args.time_limit,
        num_workers=args.workers,
        max_retries=args.max_retries,
        route_config=RouteOrchestratorConfig(
            grid_step_um=args.astar_step_um,
            max_rounds=args.ripup_rounds,
        ),
    )
    result = solve_layout(args.layout, options=options)

    geom = result.geometry
    audit = result.audit
    sa = result.sa

    for edge_id, reason in result.skipped_locked_edges:
        print(f"[warn] skipped locked length on {edge_id}: {reason}", file=sys.stderr)

    sa_msg = (
        f" sa(E:{sa.initial_energy:.1f}->{sa.final_energy:.1f}, "
        f"acc={sa.accepted}/{sa.accepted + sa.rejected})"
        if sa is not None
        else " sa=off"
    )
    astar_msg = (
        f" astar(routed={len(result.astar.routed_edges)},"
        f"failed={len(result.astar.failed_edges)})"
        if not args.no_astar
        else " astar=off"
    )
    if result.route is not None:
        oct_msg = (
            f" oct(routed={len(result.route.routed)},"
            f"unrouted={len(result.route.unrouted)},"
            f"rounds={result.route.rounds_used},"
            f"overshoot={len(result.route.overshoot_edges)})"
        )
    else:
        oct_msg = " oct=off"
    print(
        f"status={geom.solve_status} attempts={result.attempts} "
        f"wall={geom.solve_wall_seconds:.3f}s "
        f"locked_within_tol={audit.locked_edges_within_tolerance} "
        f"no_overlap_pass={audit.no_overlap_pass} "
        f"max_len_err={audit.max_length_error_fraction * 100:.3f}%"
        f"{sa_msg}{astar_msg}{oct_msg}"
    )

    if not args.quiet:
        for c in audit.length_checks:
            print(
                f"  {c.edge_id}: target={c.target_length:.4f}mm "
                f"actual={c.actual_length:.4f}mm err="
                f"{c.error_fraction * 100:+.3f}%"
            )
        if audit.overlap_pairs:
            print("  overlap pairs:")
            for o in audit.overlap_pairs:
                print(f"    {o.edge_a} <-> {o.edge_b}: {o.overlap_um}µm")
        if result.astar.failed_edges:
            print(f"  astar failed: {result.astar.failed_edges}")

    if args.svg_out is not None:
        args.svg_out.parent.mkdir(parents=True, exist_ok=True)
        args.svg_out.write_text(render_geometry_svg(geom), encoding="utf-8")
        print(f"svg -> {args.svg_out}")

    bend_report = None
    meander_report = None
    drc_report = None
    lvs_report = None
    rendered_geom = geom
    if args.bend:
        rendered_geom, bend_report = apply_bends(geom, result.ir)
        print(
            f"bend: bended={len(bend_report.bended_edges)} "
            f"skip={len(bend_report.skipped_edges)} "
            f"warn={len(bend_report.warnings)}"
        )

    if args.meander:
        rendered_geom, meander_report = apply_meanders(rendered_geom, result.ir)
        print(
            f"meander: applied={meander_report.total_meandered} "
            f"skip={len(meander_report.skipped_edges)} "
            f"warn={len(meander_report.warnings)}"
        )

    drc_report = run_drc(
        rendered_geom,
        result.ir,
        known_overlap_pairs=frozenset(
            (o.edge_a, o.edge_b) for o in audit.overlap_pairs
        ),
    )
    print(
        f"drc: critical={drc_report.critical_count} "
        f"warning={drc_report.warning_count}"
    )

    lvs_report = run_lvs(result.ir, rendered_geom)
    if lvs_report.skipped:
        print(f"lvs: skipped ({lvs_report.reason})")
    else:
        print(
            f"lvs: nets={len(lvs_report.checked_nets)} "
            f"mismatches={len(lvs_report.mismatches)}"
        )

    if args.drc_out is not None:
        args.drc_out.parent.mkdir(parents=True, exist_ok=True)
        args.drc_out.write_text(
            json.dumps(_drc_dict(drc_report), indent=2),
            encoding="utf-8",
        )
        print(f"drc-report -> {args.drc_out}")

    if args.lvs_out is not None:
        args.lvs_out.parent.mkdir(parents=True, exist_ok=True)
        args.lvs_out.write_text(
            json.dumps(_lvs_dict(lvs_report), indent=2),
            encoding="utf-8",
        )
        print(f"lvs-report -> {args.lvs_out}")

    if args.final_svg is not None:
        args.final_svg.parent.mkdir(parents=True, exist_ok=True)
        layout_for_pads = None
        if args.show_pads:
            try:
                layout_for_pads = load_v33_layout(args.layout)
            except Exception as exc:  # pragma: no cover — best-effort
                print(f"[warn] pad rendering disabled: {exc}", file=sys.stderr)
        args.final_svg.write_text(
            render_full_layout(
                rendered_geom,
                bend=bend_report,
                drc=drc_report,
                lvs=lvs_report,
                layout=layout_for_pads,
                show_pads=args.show_pads,
            ),
            encoding="utf-8",
        )
        print(f"final-svg -> {args.final_svg}")

    crossing_report = None
    if args.crossing_report_json is not None or args.crossing_report_md is not None:
        semantic_count = len(result.skipped_locked_edges) + sum(
            1
            for c in audit.length_checks
            if c.error_fraction is not None and abs(c.error_fraction) >= 0.05
        )
        crossing_report = analyze_crossings(
            geom,
            result.ir,
            audit.overlap_pairs,
            semantic_issue_count=semantic_count,
        )
        print(
            f"crossing: pairs={crossing_report.total_pairs} "
            f"critical={crossing_report.critical_pair_count} "
            f"gaps={len(crossing_report.gaps)} "
            f"hotspots={len(crossing_report.region_hotspots)}"
        )
    if crossing_report is not None and args.crossing_report_json is not None:
        args.crossing_report_json.parent.mkdir(parents=True, exist_ok=True)
        args.crossing_report_json.write_text(
            crossing_to_json(crossing_report), encoding="utf-8"
        )
        print(f"crossing-json -> {args.crossing_report_json}")
    if crossing_report is not None and args.crossing_report_md is not None:
        args.crossing_report_md.parent.mkdir(parents=True, exist_ok=True)
        args.crossing_report_md.write_text(
            crossing_to_md(crossing_report), encoding="utf-8"
        )
        print(f"crossing-md   -> {args.crossing_report_md}")

    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        report = _report_dict(result)
        if bend_report is not None:
            report["bend"] = {
                "bended_edges": list(bend_report.bended_edges),
                "skipped_edges": list(bend_report.skipped_edges),
                "warnings": list(bend_report.warnings),
            }
        if meander_report is not None:
            report["meander"] = {
                "meandered_edges": [
                    {
                        "edge_id": r.edge_id,
                        "original_length_mm": r.original_length_mm,
                        "target_length_mm": r.target_length_mm,
                        "achieved_length_mm": r.achieved_length_mm,
                        "n_loops": r.n_loops,
                        "loop_height_mm": r.loop_height_mm,
                    }
                    for r in meander_report.meandered_edges
                ],
                "skipped_edges": [r.edge_id for r in meander_report.skipped_edges],
                "warnings": list(meander_report.warnings),
            }
        report["drc"] = _drc_dict(drc_report)
        report["lvs"] = _lvs_dict(lvs_report)
        args.report_out.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        print(f"report -> {args.report_out}")

    if geom.solve_status not in ("OPTIMAL", "FEASIBLE"):
        return 3
    if not audit.locked_edges_within_tolerance:
        return 4
    if drc_report is not None and drc_report.critical_count > 0:
        return 5
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
