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
"$PYTHON" -m tools.cpsat_solve rf_layout_simplified.yaml \
  --time-limit 10 --workers 8 --quiet \
  --svg-out out/PA_Module_Simplified.geom.svg \
  --report-out out/PA_Module_Simplified.geom.json
"$PYTHON" -m tools.pcb_solve rf_layout_simplified.yaml \
  --time-limit 10 --workers 8 --max-retries 5 --quiet \
  --svg-out out/PA_Module_Simplified.m5.svg \
  --report-out out/PA_Module_Simplified.m5.json
"$PYTHON" -m tools.pcb_solve rf_layout_simplified.yaml \
  --time-limit 10 --workers 8 --max-retries 5 --quiet \
  --bend \
  --drc-out out/PA_Module_Simplified.m6.drc.json \
  --lvs-out out/PA_Module_Simplified.m6.lvs.json \
  --final-svg out/PA_Module_Simplified.m6.final.svg \
  --report-out out/PA_Module_Simplified.m6.json

# DoD assertion: DRC critical must be 0 on the PA reference design.
"$PYTHON" - <<'PY'
import json, sys
from pathlib import Path
rep = json.loads(Path("out/PA_Module_Simplified.m6.drc.json").read_text())
crit = rep["critical"]
print(f"M6 DRC critical={crit} warning={rep['warning']}")
if crit != 0:
    sys.exit(f"DoD breach: M6 DRC critical={crit} (must be 0)")
PY
echo "verify_m6: OK"
