"""Phase 8.5 step 4/5: dtype propagation evidence + float32 vs float64 re-run.

Runs under .venv-engine (Python 3.9). Prints, for a float32 and a float64
solve on the same spot: the config dtype, the engine-reported dtype
(build response via ``RealEngine.describe``), and the tensor dtypes of the
strategy and regret buffers. Then re-runs the Phase-7 spots at both dtypes
with unrounded results plus a second identical float32 run for noise.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.phase7.spots import SPOTS

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "float32_rerun.json")

PROBE_SPOT = "river-dry-cap0"
RERUN_SPOTS = ["toy-check", "toy-bet", "river-dry-cap0", "river-wet-cap0"]
NOISE_SPOT = "toy-check"


def buffer_dtypes(dtype: str) -> dict:
    """Build a spot at ``dtype`` and report config + buffer dtypes."""
    from holdem_solver.engine_worker.real import RealEngine

    spot = next(s for s in SPOTS if s.game_name == PROBE_SPOT)
    engine = RealEngine()
    engine.build(spot.to_dict(), dtype=dtype, device="cuda")
    solver = engine._require_solver()
    flat = solver.average_strategy_flat()
    out = {
        "requested": dtype,
        "config_device_dtype": str(engine._config.device.dtype),
        "describe": engine.describe()["dtype"],
        "flat_strategy": str(flat.dtype),
        "regret_sum": str(solver.regret_sum.dtype),
        "strategy_sum": str(solver.strategy_sum.dtype),
    }
    engine.close()
    return out


def main():
    os.makedirs(RUNS, exist_ok=True)
    from scripts.phase7.ours import solve_ours

    evidence = {d: buffer_dtypes(d) for d in ("float32", "float64")}
    for d, e in evidence.items():
        print(d, json.dumps(e), flush=True)

    results = {"buffer_dtypes": evidence, "spots": {}}
    for name in RERUN_SPOTS:
        spot = next(s for s in SPOTS if s.game_name == name)
        row = {}
        for dtype in ("float64", "float32"):
            r = solve_ours(spot, dtype=dtype, accuracy_pct=0.5)
            row[dtype] = {
                "ev_p0": repr(r["ev_p0"]),
                "exploitability_pct": repr(r["exploitability_pct"]),
                "exploitability_chips": repr(r["exploitability_chips"]),
                "iterations": r["iterations"],
            }
            print(name, dtype, row[dtype], flush=True)
        results["spots"][name] = row

    noise = []
    for i in range(2):
        spot = next(s for s in SPOTS if s.game_name == NOISE_SPOT)
        r = solve_ours(spot, dtype="float32", accuracy_pct=0.5)
        noise.append({
            "ev_p0": repr(r["ev_p0"]),
            "exploitability_pct": repr(r["exploitability_pct"]),
            "iterations": r["iterations"],
        })
        print("noise run", i, noise[-1], flush=True)
    results["float32_run_to_run"] = noise

    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
