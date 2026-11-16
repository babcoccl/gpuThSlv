#!/usr/bin/env bash
# Engine-runtime checks: torch device/matmul verification, then Kuhn smoke on GPU 0 and GPU 1.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE_PYTHON="$ROOT_DIR/.venv-engine/bin/python"
LOG="$ROOT_DIR/scripts/phase0/logs/engine_checks.txt"
mkdir -p "$(dirname "$LOG")"

export PYTHONPATH="$ROOT_DIR/third_party/GPU-CFR"
export PYTHONDONTWRITEBYTECODE=1

: > "$LOG"

section() {
    printf '\n=== %s ===\n' "$1" | tee -a "$LOG"
}

section "exact torch check"
"$ENGINE_PYTHON" -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_capability(0))" 2>&1 | tee -a "$LOG"

section "engine torch check (all visible devices)"
"$ENGINE_PYTHON" "$ROOT_DIR/scripts/phase0/engine_torch_check.py" 2>&1 | tee -a "$LOG"

for gpu in 0 1; do
    section "engine Kuhn smoke CUDA_VISIBLE_DEVICES=$gpu"
    CUDA_VISIBLE_DEVICES="$gpu" "$ENGINE_PYTHON" "$ROOT_DIR/scripts/phase0/engine_kuhn_smoke.py" 2>&1 | tee -a "$LOG"
done
