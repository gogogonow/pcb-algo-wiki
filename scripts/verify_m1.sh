#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON=.venv/bin/python
  else
    PYTHON=python3
  fi
fi

"$PYTHON" -m black --check src tests tools
"$PYTHON" -m ruff check src tests tools
"$PYTHON" -m mypy src
"$PYTHON" -m pytest -q
"$PYTHON" -m tools.schema_check rf_layout_simplified.yaml
"$PYTHON" -m tools.topology_viz rf_layout_simplified.yaml
