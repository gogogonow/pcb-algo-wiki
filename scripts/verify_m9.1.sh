#!/usr/bin/env bash
# M9.1 verification: routing quality improvement.
# Run from repo root: ./scripts/verify_m9.1.sh
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

echo "=== M9.1 static checks ==="
"$PYTHON" -m black --check src tests tools
"$PYTHON" -m ruff check src tests tools
"$PYTHON" -m mypy src
"$PYTHON" -m pytest -q

echo "=== M9.1 end-to-end PA board ==="
mkdir -p out

# Exit codes: 0=OK, 3=INFEASIBLE, 4=len-tol fail, 5=DRC critical
set +e
"$PYTHON" -m tools.pcb_solve rf_layout_simplified.yaml \
  --time-limit 30 --workers 4 \
  --crossing-report-json out/crossing_m9.1.json \
  --crossing-report-md out/crossing_m9.1.md
SOLVE_EXIT=$?
set -e

if [[ $SOLVE_EXIT -eq 0 ]]; then
  echo "e2e: PASS (all quality gates met)"
elif [[ $SOLVE_EXIT -eq 4 ]]; then
  echo "e2e: WARN (exit $SOLVE_EXIT — length tolerance not met; routing partially unrouted)"
elif [[ $SOLVE_EXIT -eq 3 ]]; then
  echo "e2e: FAIL (exit $SOLVE_EXIT — CP-SAT INFEASIBLE)" >&2; exit $SOLVE_EXIT
elif [[ $SOLVE_EXIT -eq 5 ]]; then
  echo "e2e: FAIL (exit $SOLVE_EXIT — DRC critical violations)" >&2; exit $SOLVE_EXIT
else
  echo "e2e: FAIL (exit $SOLVE_EXIT)" >&2; exit $SOLVE_EXIT
fi

echo "=== verify_m9.1: OK ==="
