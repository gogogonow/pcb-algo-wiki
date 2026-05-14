"""Run the M2 Frontend Compiler on a v3.3 layout YAML."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
import re
import sys
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from frontend import artifact_to_dict, compile_layout


def main(argv: list[str] | None = None) -> int:
    """Compile a v3.3 layout into the M2 frontend artifact."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", help="Path to the v3.3 layout YAML file")
    parser.add_argument(
        "--out",
        default=None,
        help="Optional JSON output path (defaults to out/{project}.frontend.json)",
    )
    parser.add_argument(
        "--lint-only",
        action="store_true",
        help="Only run schema lint and print the report; skip writing JSON.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input_path)
    if not input_path.exists():
        parser.error(f"input file not found: {input_path}")

    try:
        artifact = compile_layout(input_path)
    except yaml.YAMLError as exc:
        parser.error(f"invalid YAML in {input_path}: {exc}")
    except ValidationError as exc:
        parser.error(f"schema validation failed for {input_path}: {exc}")

    _print_summary(artifact)

    if args.lint_only:
        return 0 if artifact.lint_report.is_clean else 1

    output_path = Path(args.out) if args.out else _default_output_path(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact_to_dict(artifact), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote: {output_path}")
    return 0 if artifact.lint_report.is_clean else 1


def _print_summary(artifact: Any) -> None:
    edges_hist = artifact.edges_by_class()
    nodes_hist = artifact.nodes_by_type()
    print(f"project: {artifact.project_name or '<unknown>'}")
    print(f"fixed_pads: {len(artifact.fixed_terminals)}")
    print(f"uv_components: {len(artifact.uv_components)}")
    print(f"obstacles: {len(artifact.obstacles)}")
    print(
        "edges: "
        f"locked={edges_hist.get('rf_constrained_locked', 0)} "
        f"free={edges_hist.get('rf_constrained_free', 0)} "
        f"flex={edges_hist.get('flexible_path', 0)} "
        f"other={sum(v for k, v in edges_hist.items() if k not in {'rf_constrained_locked', 'rf_constrained_free', 'flexible_path'})}"
    )
    parts = [f"{k}={v}" for k, v in sorted(nodes_hist.items())]
    print("nodes: " + (" ".join(parts) if parts else "(none)"))
    print(
        f"lint: repairs={len(artifact.lint_report.repairs)} "
        f"warnings={len(artifact.lint_report.warnings)} "
        f"errors={len(artifact.lint_report.errors)}"
    )


def _default_output_path(input_path: Path) -> Path:
    data = yaml.safe_load(input_path.read_text(encoding="utf-8"))
    project_name = _project_name(data)
    return Path("out") / f"{project_name}.frontend.json"


def _project_name(data: object) -> str:
    if isinstance(data, Mapping):
        metadata = data.get("metadata")
        if isinstance(metadata, Mapping):
            project_name = metadata.get("project_name")
            if isinstance(project_name, str) and project_name.strip():
                return _safe_filename(project_name.strip())
    return "frontend"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("._-")
    return safe or "frontend"


if __name__ == "__main__":
    sys.exit(main())
