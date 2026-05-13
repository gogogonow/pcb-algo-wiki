"""Report summary counts for a v3.3 layout YAML."""

from __future__ import annotations

import argparse

from schema import load_v33_layout


def main(argv: list[str] | None = None) -> int:
    """Load a v3.3 layout and print project/count summaries."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", help="Path to the v3.3 layout YAML file")
    args = parser.parse_args(argv)

    layout = load_v33_layout(args.input_path)

    print(f"project: {layout.metadata.project_name or '<unknown>'}")
    print(f"components: {len(layout.components)}")
    print(f"footprints: {len(layout.footprints)}")
    print(f"nodes: {len(layout.nodes)}")
    print(f"terminals: {len(layout.terminals)}")
    print(f"edges: {len(layout.edges)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
