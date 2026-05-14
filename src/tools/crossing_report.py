"""``crossing_report`` CLI — emit the M8 走线交叉诊断 报告.

Re-uses :func:`solver.orchestrator.solve_layout` to produce a fresh audit and
then runs :func:`postproc.crossing_analysis.analyze_crossings` on the
overlap pairs. Output formats: JSON (machine) and Markdown (human).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from postproc import analyze_crossings
from postproc.crossing_report_io import to_json, to_markdown
from solver import (
    DEFAULT_NUM_WORKERS,
    DEFAULT_TIME_LIMIT_S,
    OrchestratorOptions,
    solve_layout,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crossing_report",
        description=(
            "Run the full pipeline on a v3.3 YAML and emit the M8 crossing "
            "diagnostics report (JSON and/or Markdown)."
        ),
    )
    p.add_argument("layout", type=Path, help="Path to v3.3 YAML.")
    p.add_argument("--json", type=Path, default=None, help="JSON output path.")
    p.add_argument("--md", type=Path, default=None, help="Markdown output path.")
    p.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT_S,
        help="CP-SAT wall-time per attempt (seconds).",
    )
    p.add_argument("--workers", type=int, default=DEFAULT_NUM_WORKERS)
    p.add_argument("--no-sa", action="store_true")
    p.add_argument("--no-astar", action="store_true")
    p.add_argument("--max-retries", type=int, default=5)
    return p


def _semantic_issue_count(result) -> int:  # type: ignore[no-untyped-def]
    """Heuristic: number of edges with infeasible length / large length error.

    These map onto the M7 ``length_infeasible_short`` / ``meander_required``
    findings without re-invoking the frontend lint (which needs additional
    expand/normalize plumbing).
    """

    skipped = len(result.skipped_locked_edges)
    big_error = sum(
        1
        for c in result.audit.length_checks
        if c.error_fraction is not None and abs(c.error_fraction) >= 0.05
    )
    return skipped + big_error


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.layout.exists():
        print(f"layout not found: {args.layout}", file=sys.stderr)
        return 2
    if args.json is None and args.md is None:
        print("at least one of --json / --md required", file=sys.stderr)
        return 2

    options = OrchestratorOptions(
        use_sa=not args.no_sa,
        use_astar=not args.no_astar,
        time_limit_s=args.time_limit,
        num_workers=args.workers,
        max_retries=args.max_retries,
    )
    result = solve_layout(args.layout, options=options)
    semantic_count = _semantic_issue_count(result)
    report = analyze_crossings(
        result.geometry,
        result.ir,
        result.audit.overlap_pairs,
        semantic_issue_count=semantic_count,
    )

    print(
        f"crossing_report: project={report.project} "
        f"total_pairs={report.total_pairs} "
        f"critical={report.critical_pair_count} "
        f"gaps={len(report.gaps)} "
        f"hotspots={len(report.region_hotspots)}"
    )

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(to_json(report), encoding="utf-8")
        print(f"json -> {args.json}")
    if args.md is not None:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(to_markdown(report), encoding="utf-8")
        print(f"md   -> {args.md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
