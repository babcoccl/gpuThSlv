#!/usr/bin/env bash
# Phase 1 gate: run the engine pytest suite once per visible RTX 5060 Ti.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE_PYTHON="$ROOT_DIR/.venv-engine/bin/python"
LOG_DIR="$ROOT_DIR/scripts/phase1/logs"
mkdir -p "$LOG_DIR"

cd "$ROOT_DIR"
export OMP_NUM_THREADS=8
export PYTHONDONTWRITEBYTECODE=1

for gpu in 0 1; do
    log="$LOG_DIR/pytest_gpu${gpu}.txt"
    CUDA_VISIBLE_DEVICES="$gpu" "$ENGINE_PYTHON" -m pytest -q >"$log" 2>&1
    printf 'gpu%s %s\n' "$gpu" "$(tail -n 1 "$log")"
done
