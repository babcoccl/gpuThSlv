#!/usr/bin/env bash
# Phase 5 gate: engine suite per GPU, then the full API suite (incl. GPU tests).
set -uo pipefail
cd "$(dirname "$0")/../.."
LOG_DIR=scripts/phase5/logs
mkdir -p "$LOG_DIR"
for gpu in 0 1; do
    CUDA_VISIBLE_DEVICES="$gpu" .venv-engine/bin/python -m pytest -q -rs >"$LOG_DIR/pytest_engine_gpu${gpu}.txt" 2>&1
    tail -1 "$LOG_DIR/pytest_engine_gpu${gpu}.txt"
done
# Workers spawned by the engine run exit via their parent watchdog; let them
# release the per-GPU socket locks before the API suite claims them.
for _ in $(seq 50); do pgrep -f holdem_solver.engine_worker >/dev/null || break; sleep 0.2; done
.venv-api/bin/python -m pytest -q -rs -m "gpu or not gpu" tests 2>&1 | grep -v '^{"ts"' >"$LOG_DIR/pytest_api.txt"
tail -1 "$LOG_DIR/pytest_api.txt"
