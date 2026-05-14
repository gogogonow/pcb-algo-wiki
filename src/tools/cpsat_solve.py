"""``cpsat_solve`` CLI — compile YAML → SolverIR → CP-SAT solve → SVG/JSON.

Usage::

    cpsat_solve rf_layout_simplified.yaml \
        --time-limit 10 --workers 8 \
        --svg-out out/PA_Module_Simplified.geom.svg \
        --report-out out/PA_Module_Simplified.geom.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from frontend.compile import compile_layout
from frontend.solver_ir import compile_solver_ir
from postproc.geom_svg import render_geometry_svg
from solver import audit_geometry, build_model, extract_geometry, solve_model


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cpsat_solve",
        description=(
            "Solve an rf_microwave_layout v3.3 YAML with the M4 CP-SAT model "
            "and emit a GeometryIR + optional SVG."
        ),
    )
    p.add_argument("layout", type=Path, help="Path to v3.3 YAML.")
    p.add_argument(
        "--time-limit",
        type=float,
        default=10.0,
        help="Solver wall-time limit (seconds). Default 10.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=8,
        help="num_search_workers. Default 8.",
    )
    p.add_argument(
        "--svg-out",
        type=Path,
        default=None,
        help="Optional SVG output path. Parent dirs are created.",
    )
    p.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Optional JSON report (status, length errors, overlap pairs).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-edge length error printout (still prints summary).",
    )
    return p


def _report_dict(geom, audit) -> dict:
    return {
        "project": geom.project,
        "solve_status": geom.solve_status,
        "wall_seconds": geom.solve_wall_seconds,
        "objective": geom.objective_value,
        "max_length_error_fraction": audit.max_length_error_fraction,
        "locked_edges_within_tolerance": audit.locked_edges_within_tolerance,
        "no_overlap_pass": audit.no_overlap_pass,
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

    artifact = compile_layout(str(args.layout))
    ir = compile_solver_ir(str(args.layout))
    cpsat = build_model(ir, artifact)
    if cpsat.skipped_locked_edges:
        for edge_id, reason in cpsat.skipped_locked_edges:
            print(
                f"[warn] skipped locked length on {edge_id}: {reason}", file=sys.stderr
            )

    result = solve_model(cpsat, time_limit_s=args.time_limit, num_workers=args.workers)
    geom = extract_geometry(ir=ir, artifact=artifact, cpsat=cpsat, result=result)
    audit = audit_geometry(
        ir,
        geom,
        skip_length_edges={eid for eid, _ in cpsat.skipped_locked_edges},
        resolved_endpoints=cpsat.edge_endpoint_resolved,
    )

    print(
        f"status={geom.solve_status} wall={geom.solve_wall_seconds:.3f}s "
        f"locked_within_tol={audit.locked_edges_within_tolerance} "
        f"no_overlap_pass={audit.no_overlap_pass} "
        f"max_len_err={audit.max_length_error_fraction * 100:.3f}%"
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

    if args.svg_out is not None:
        args.svg_out.parent.mkdir(parents=True, exist_ok=True)
        args.svg_out.write_text(render_geometry_svg(geom), encoding="utf-8")
        print(f"svg -> {args.svg_out}")

    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(_report_dict(geom, audit), indent=2),
            encoding="utf-8",
        )
        print(f"report -> {args.report_out}")

    if geom.solve_status not in ("OPTIMAL", "FEASIBLE"):
        return 3
    if not audit.locked_edges_within_tolerance:
        return 4
    # M4 returns success even when audit.no_overlap_pass is False:
    # NoOverlap repair is the responsibility of M5 SA refinement (overlap
    # pairs are reported above for follow-up).
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
