#!/usr/bin/env bash
# M8 verification: crossing diagnostics + pad rendering.
# Runs the full M7 pipeline plus M8 reports and asserts DoD.
# Run from repo root: ./scripts/verify_m8.sh
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

mkdir -p out

# End-to-end PA solve with M8 crossing diagnostics + pad rendering
"$PYTHON" -m tools.pcb_solve rf_layout_simplified.yaml \
  --time-limit 10 --workers 8 --max-retries 5 --quiet \
  --bend --meander --show-pads \
  --drc-out out/PA_Module_Simplified.m8.drc.json \
  --lvs-out out/PA_Module_Simplified.m8.lvs.json \
  --final-svg out/PA_Module_Simplified.m8.final.svg \
  --crossing-report-json out/PA_Module_Simplified.m8.crossing.json \
  --crossing-report-md out/PA_Module_Simplified.m8.crossing.md \
  --report-out out/PA_Module_Simplified.m8.json

# Standalone crossing_report CLI (sanity)
"$PYTHON" -m tools.crossing_report rf_layout_simplified.yaml \
  --time-limit 5 --workers 4 \
  --json out/PA_Module_Simplified.m8.crossing-cli.json \
  --md   out/PA_Module_Simplified.m8.crossing-cli.md

# DoD assertions
"$PYTHON" - <<'PY'
import json, re, sys
from pathlib import Path

drc  = json.loads(Path("out/PA_Module_Simplified.m8.drc.json").read_text())
crossing = json.loads(Path("out/PA_Module_Simplified.m8.crossing.json").read_text())
svg = Path("out/PA_Module_Simplified.m8.final.svg")
md  = Path("out/PA_Module_Simplified.m8.crossing.md")

# 1. DRC critical = 0 (no regression vs M7)
if drc["critical"] != 0:
    sys.exit(f"DoD breach: M8 DRC critical={drc['critical']} (must be 0)")

# 2. crossing report has at least one GAP
gaps = crossing.get("gaps", [])
if len(gaps) < 1:
    sys.exit("DoD breach: M8 crossing report has 0 GAPs (expected ≥1)")

# 3. crossing report has total_pairs > 0 (M8 only diagnoses; doesn't fix)
if crossing.get("total_pairs", 0) == 0:
    sys.exit("DoD breach: M8 crossing report total_pairs == 0 (sanity)")

# 4. final SVG renders ≥20 pads (PA layout has 22)
if not svg.exists():
    sys.exit("DoD breach: M8 final SVG missing")
n_pads = len(re.findall(r"<polygon\b", svg.read_text(encoding="utf-8")))
if n_pads < 20:
    sys.exit(f"DoD breach: M8 final SVG has {n_pads} pads (expected ≥20)")

# 5. Markdown report has all four sections
md_text = md.read_text(encoding="utf-8")
for section in ("根因分布", "区域热点", "算法 GAP", "重叠对明细"):
    if section not in md_text:
        sys.exit(f"DoD breach: MD report missing section {section!r}")

print(
    f"M8 verify: pairs={crossing['total_pairs']} "
    f"critical={crossing['critical_pair_count']} "
    f"gaps={len(gaps)} pads={n_pads} OK"
)
PY
echo "verify_m8: OK"
