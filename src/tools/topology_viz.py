"""Generate a topology SVG from a YAML topology description."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
import re

import yaml  # type: ignore[import-untyped]

from topology.loaders import load_topology_graph
from topology.render_svg import render_topology_svg


def main(argv: list[str] | None = None) -> int:
    """Generate an SVG topology diagram from a YAML input file."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", help="Path to the topology YAML file")
    parser.add_argument(
        "output_path",
        nargs="?",
        help="Optional SVG output path (defaults to out/{project}.topology.svg)",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input_path)
    output_path = (
        Path(args.output_path) if args.output_path else _default_output_path(input_path)
    )

    graph = load_topology_graph(input_path)
    svg = render_topology_svg(graph)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    print(output_path)
    return 0


def _default_output_path(input_path: Path) -> Path:
    data = yaml.safe_load(input_path.read_text(encoding="utf-8"))
    project_name = _project_name(data)
    return Path("out") / f"{project_name}.topology.svg"


def _project_name(data: object) -> str:
    if isinstance(data, Mapping):
        metadata = data.get("metadata")
        if isinstance(metadata, Mapping):
            project_name = metadata.get("project_name")
            if isinstance(project_name, str) and project_name.strip():
                return _safe_filename(project_name.strip())
    return "topology"


def _safe_filename(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("._-")
    return safe_value or "topology"


if __name__ == "__main__":
    raise SystemExit(main())
