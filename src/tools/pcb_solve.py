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

from postproc.geom_svg import render_geometry_svg
from solver import (
    DEFAULT_NUM_WORKERS,
    DEFAULT_TIME_LIMIT_S,
    OrchestratorOptions,
    solve_layout,
)


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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.layout.exists():
        print(f"layout not found: {args.layout}", file=sys.stderr)
        return 2

    options = OrchestratorOptions(
        use_sa=not args.no_sa,
        use_astar=not args.no_astar,
        time_limit_s=args.time_limit,
        num_workers=args.workers,
        max_retries=args.max_retries,
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
    print(
        f"status={geom.solve_status} attempts={result.attempts} "
        f"wall={geom.solve_wall_seconds:.3f}s "
        f"locked_within_tol={audit.locked_edges_within_tolerance} "
        f"no_overlap_pass={audit.no_overlap_pass} "
        f"max_len_err={audit.max_length_error_fraction * 100:.3f}%"
        f"{sa_msg}{astar_msg}"
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

    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(_report_dict(result), indent=2),
            encoding="utf-8",
        )
        print(f"report -> {args.report_out}")

    if geom.solve_status not in ("OPTIMAL", "FEASIBLE"):
        return 3
    if not audit.locked_edges_within_tolerance:
        return 4
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
