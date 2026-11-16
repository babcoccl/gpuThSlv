#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENGINE_PYTHON="$ROOT_DIR/.venv-engine/bin/python"
LOG_DIR="$ROOT_DIR/scripts/phase0/logs"
mkdir -p "$LOG_DIR"

export PYTHONPATH="$ROOT_DIR/third_party/GPU-CFR"
export PYTHONDONTWRITEBYTECODE=1

"$ENGINE_PYTHON" - <<'PY' > "$LOG_DIR/gpu_cfr_api_help.txt"
import importlib
import pydoc
import traceback

modules = [
    "gpu_cfr",
    "gpu_cfr.config",
    "gpu_cfr.devices",
    "gpu_cfr.games.kuhn",
    "gpu_cfr.games.hunl_river",
    "gpu_cfr.games.hunl_turn",
    "gpu_cfr.games.registry",
    "gpu_cfr.solvers.stagewise",
    "gpu_cfr.solvers.lifecycle",
    "gpu_cfr.solvers.update_rules",
    "gpu_cfr.eval.exploitability",
    "gpu_cfr.eval.strategy_export",
    "gpu_cfr.benchmarks.iter_bench",
]

for name in modules:
    print("=" * 100)
    print("MODULE", name)
    print("=" * 100)
    try:
        module = importlib.import_module(name)
        print("file:", getattr(module, "__file__", None))
        print("__all__:", getattr(module, "__all__", None))
        print("public dir:", [x for x in dir(module) if not x.startswith("_")])
        pydoc.help(module)
    except Exception:
        traceback.print_exc()
PY

printf 'wrote %s\n' "$LOG_DIR/gpu_cfr_api_help.txt"
