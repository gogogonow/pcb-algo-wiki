#!/usr/bin/env bash
set -euo pipefail

python -m black --check src tests tools
python -m ruff check src tests tools
python -m mypy src
python -m pytest -q
python tools/schema_check.py rf_layout_simplified.yaml
python tools/topology_viz.py rf_layout_simplified.yaml
