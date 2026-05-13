#!/usr/bin/env bash
set -euo pipefail

python3 -m black --check src tests tools
python3 -m ruff check src tests tools
python3 -m mypy src
python3 -m pytest -q
python3 tools/topology_viz.py rf_layout_simplified.yaml
