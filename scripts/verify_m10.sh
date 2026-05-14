#!/usr/bin/env bash
# M10 quality gate: black + ruff + mypy + pytest + v2 smoke run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY=".venv/bin/python"

echo "=== black ==="
"$PY" -m black --check src tests tools

echo "=== ruff ==="
"$PY" -m ruff check src tests tools

echo "=== mypy ==="
"$PY" -m mypy

echo "=== pytest ==="
"$PY" -m pytest -q

echo "=== pcb_solve_v2 smoke ==="
mkdir -p out
"$PY" -m tools.pcb_solve_v2 rf_layout_simplified.yaml \
    --out-dir out

echo "=== M10 quality gate PASSED ==="
