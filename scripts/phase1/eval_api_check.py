#!/usr/bin/env python3
"""Phase 1 check: which gpu_cfr eval/export APIs work without project_llm_opp_prior."""

import importlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "third_party" / "GPU-CFR"))

for module_name in [
    "gpu_cfr.eval.exploitability",
    "gpu_cfr.eval.best_response",
    "gpu_cfr.eval.strategy_export",
    "gpu_cfr.eval.metrics",
]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - report the exact failure mode
        print(f"{module_name}: FAIL {type(exc).__name__}: {exc}")
    else:
        print(f"{module_name}: OK")

from gpu_cfr.eval.strategy_export import export_internal_to_kuhn  # noqa: E402
from holdem_solver.engine_worker import SolverEngine  # noqa: E402

engine = SolverEngine("kuhn_poker", device="cpu", variant="cfr_plus", seed=42, iterations=2000)
try:
    result = engine.solve(iterations=2000)
    exported = export_internal_to_kuhn(engine.get_strategy())
    print(f"kuhn_export_entries={len(exported)}")
    print(f"kuhn_exploitability={result.exploitability:.9f}")
    print(f"kuhn_profile_value_p0={result.profile_value_p0:.9f}")
finally:
    engine.close()
