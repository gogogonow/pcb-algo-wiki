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

# CLI subprocess tests invoke `python3` directly; prefer the project venv
# binaries so editable installs (pydantic, etc.) resolve.
if [[ -d .venv/bin ]]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi

"$PYTHON" -m black --check src tests tools
"$PYTHON" -m ruff check src tests tools
"$PYTHON" -m mypy src
"$PYTHON" -m pytest -q
"$PYTHON" -m tools.schema_check rf_layout_simplified.yaml
"$PYTHON" -m tools.topology_viz rf_layout_simplified.yaml
"$PYTHON" -m tools.frontend_compile rf_layout_simplified.yaml \
  --out out/PA_Module_Simplified.frontend.json
"$PYTHON" -m tools.solver_ir rf_layout_simplified.yaml \
  --out out/PA_Module_Simplified.solver.json
