#!/usr/bin/env bash
# M7 verification script: semantic lint + meander routing + DRC geometry upgrade
# Run from repo root: ./scripts/verify_m7.sh
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

# End-to-end PA solve with bend + meander + DRC/LVS (M7 full pipeline)
"$PYTHON" -m tools.pcb_solve rf_layout_simplified.yaml \
  --time-limit 10 --workers 8 --max-retries 5 --quiet \
  --bend --meander \
  --drc-out out/PA_Module_Simplified.m7.drc.json \
  --lvs-out out/PA_Module_Simplified.m7.lvs.json \
  --final-svg out/PA_Module_Simplified.m7.final.svg \
  --report-out out/PA_Module_Simplified.m7.json

# DoD assertions
"$PYTHON" - <<'PY'
import json, sys
from pathlib import Path

rep  = json.loads(Path("out/PA_Module_Simplified.m7.json").read_text())
drc  = json.loads(Path("out/PA_Module_Simplified.m7.drc.json").read_text())

# 1. DRC critical = 0
crit = drc["critical"]
print(f"M7 DRC critical={crit} warning={drc['warning']}")
if crit != 0:
    sys.exit(f"DoD breach: M7 DRC critical={crit} (must be 0)")

# 2. Meander section present in report
if "meander" not in rep:
    sys.exit("DoD breach: 'meander' key missing from M7 report")

applied = len(rep["meander"].get("meandered_edges", []))
print(f"M7 meander applied={applied}")

# 3. SVG output exists and is non-trivial
svg = Path("out/PA_Module_Simplified.m7.final.svg")
if not svg.exists() or svg.stat().st_size < 1024:
    sys.exit(f"DoD breach: M7 final SVG missing or too small ({svg.stat().st_size if svg.exists() else 0} bytes)")

print(f"M7 final SVG: {svg.stat().st_size} bytes  OK")
PY
echo "verify_m7: OK"
