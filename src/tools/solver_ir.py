"""Run the M3 SolverIR compiler on a v3.3 layout YAML."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from frontend import compile_solver_ir, host_match_summary, solver_ir_to_dict
from schema.solver_ir import SolverIR


def main(argv: list[str] | None = None) -> int:
    """Compile a v3.3 layout into the M3 SolverIR JSON file."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", help="Path to the v3.3 layout YAML file")
    parser.add_argument(
        "--out",
        default=None,
        help="Optional JSON output path (defaults to out/{project}.solver.json)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print summary and skip writing JSON.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input_path)
    if not input_path.exists():
        parser.error(f"input file not found: {input_path}")

    try:
        ir = compile_solver_ir(input_path)
    except yaml.YAMLError as exc:
        parser.error(f"invalid YAML in {input_path}: {exc}")
    except ValidationError as exc:
        parser.error(f"schema validation failed for {input_path}: {exc}")

    _print_summary(ir)

    if args.summary_only:
        return 0

    output_path = Path(args.out) if args.out else _default_output_path(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(solver_ir_to_dict(ir), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(f"wrote: {output_path}")
    return 0


def _print_summary(ir: SolverIR) -> None:
    summary = host_match_summary(ir)
    branches = sum(len(t.branches) for t in ir.junction_templates.values())
    print(f"project: {ir.project}")
    print(f"board: {ir.board.width} x {ir.board.height}")
    print(f"clearance: {ir.clearance}")
    print(f"terminals: {len(ir.terminals)}")
    print(f"edges: {len(ir.edges)}")
    print(
        "uv_resolutions: "
        f"unique={summary.get('unique', 0)} "
        f"ambiguous={summary.get('ambiguous', 0)} "
        f"missing={summary.get('missing', 0)}"
    )
    print(f"junction_templates: nodes={len(ir.junction_templates)} branches={branches}")


def _default_output_path(input_path: Path) -> Path:
    data = yaml.safe_load(input_path.read_text(encoding="utf-8"))
    project_name = _project_name(data)
    return Path("out") / f"{project_name}.solver.json"


def _project_name(data: object) -> str:
    if isinstance(data, Mapping):
        metadata = data.get("metadata")
        if isinstance(metadata, Mapping):
            project_name = metadata.get("project_name")
            if isinstance(project_name, str) and project_name.strip():
                return _safe_filename(project_name.strip())
    return "solver"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("._-")
    return safe or "solver"


if __name__ == "__main__":
    sys.exit(main())
