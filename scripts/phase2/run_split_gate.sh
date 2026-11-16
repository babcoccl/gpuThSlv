#!/usr/bin/env bash
# Phase 2A gate: engine suite per GPU (.venv-engine), client suite and the
# Kuhn round-trip / kill / restart evidence script (.venv-api).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="$ROOT_DIR/scripts/phase2/logs"
mkdir -p "$LOG_DIR"

cd "$ROOT_DIR"
export OMP_NUM_THREADS=8
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT_DIR"

for gpu in 0 1; do
    log="$LOG_DIR/pytest_engine_gpu${gpu}.txt"
    CUDA_VISIBLE_DEVICES="$gpu" .venv-engine/bin/python -m pytest -q >"$log" 2>&1
    printf 'engine gpu%s %s\n' "$gpu" "$(tail -n 1 "$log")"
done

log="$LOG_DIR/pytest_api.txt"
.venv-api/bin/python -m pytest -q -rs >"$log" 2>&1
printf 'api %s\n' "$(tail -n 1 "$log")"

log="$LOG_DIR/roundtrip_gate.txt"
.venv-api/bin/python scripts/phase2/roundtrip_gate.py >"$log" 2>&1
cat "$log"
