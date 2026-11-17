"""Phase 5 benchmarks: five river spot sizes, float32 vs float64, one GPU.

Run from .venv-api, one process per GPU:
    .venv-api/bin/python scripts/phase5/bench.py --gpu 0
Writes one JSON line per (spot, dtype) to scripts/phase5/logs/bench_gpu<N>.jsonl.
Each run uses a fresh worker so its peak VRAM belongs to that spot alone.

* build_seconds: the worker's build (tree + solver allocation).
* it/s: after a 50-iteration warm-up (the first solve pays one-time CUDA
  setup), a timed solve of ``timed_iterations`` with no exploitability evals.
* time to 2e-3 / 1e-3: after ``reset()``, a target-mode solve (one eval every
  ``eval_every`` iterations, eval time included) capped at ``cap_seconds``;
  the first eval at or under the threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from holdem_solver.client import EngineClient  # noqa: E402

BOARD = "KsJsTh7d2c"
NARROW = "QQ+, AKs"
MID = "TT+, AQs+, AKo, KQs"
WIDE = "77+, A9s+, KTs+, QJs, ATo+, KQo"
BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo"

# (label, range0, range1, bet_sizes, eval_every, cap_seconds, timed_iterations)
SPOTS = [
    ("xs-narrow-pot", NARROW, NARROW, "pot", 25, 120, 3000),
    ("s-mid-standard", MID, MID, "standard", 25, 120, 3000),
    ("m-wide-standard", WIDE, WIDE, "standard", 50, 180, 1000),
    ("l-broad-standard", BROAD, BROAD, "standard", 50, 240, 1000),
    ("xl-full-pot", "*", "*", "pot", 100, 300, 300),
]
TARGETS = (2e-3, 1e-3)
STOP_AT = 9e-4
MIB = 1024 * 1024


def spot(r0, r1, bets):
    return {"board": BOARD, "range0": r0, "range1": r1, "pot": 20.0, "stack": 100.0,
            "bet_sizes": bets, "max_nodes": None}


def run(gpu, label, r0, r1, bets, every, cap, timed, dtype):
    with EngineClient(gpu, build_timeout=3600) as client:
        info = client.ping()
        built = client.build(spot(r0, r1, bets), variant="cfr_plus", dtype=dtype)
        t0 = time.perf_counter()
        client.solve(iterations=50)
        warm = client.solve(iterations=timed)
        ips = warm.iterations_run / warm.elapsed_seconds
        client.reset()
        client.solve(target_exploitability=STOP_AT, progress_every=every, wait=False)
        deadline = time.monotonic() + cap
        while True:
            time.sleep(0.5)
            progress = client.progress()
            if progress.state in ("done", "cancelled", "failed"):
                break
            if time.monotonic() >= deadline:
                client.cancel()
                break
        result = client.wait(timeout=120)
        history = client.progress().exploitability_history
        hits = {}
        for target in TARGETS:
            hit = next((h for h in history if h["exploitability"] <= target), None)
            hits[str(target)] = None if hit is None else {
                "seconds": round(hit["elapsed_seconds"], 3), "iteration": hit["iteration"]}
        return {
            "gpu": gpu, "device": info.get("cuda_device_name"), "spot": label, "dtype": dtype,
            "range0": r0, "range1": r1, "bet_sizes": bets,
            "num_nodes": built.num_nodes, "num_infosets": built.num_infosets,
            "build_seconds": round(built.build_seconds, 3),
            "estimated_vram_mib": round(built.estimated_vram_bytes / MIB, 1),
            "iterations_per_second": round(ips, 1), "timed_iterations": timed,
            "peak_reserved_mib": round(result.vram.get("peak_reserved_bytes", 0) / MIB, 1),
            "peak_allocated_mib": round(result.vram.get("peak_allocated_bytes", 0) / MIB, 1),
            "converge_iterations": result.total_iterations, "converge_seconds": round(result.elapsed_seconds, 2),
            "final_exploitability": result.exploitability, "cap_seconds": cap, "eval_every": every,
            "time_to": hits, "evals": len(history), "wall_seconds": round(time.perf_counter() - t0, 1),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--only", default=None, help="comma-separated spot labels")
    args = parser.parse_args()
    out = ROOT / "scripts" / "phase5" / "logs" / ("bench_gpu%d.jsonl" % args.gpu)
    only = None if args.only is None else set(args.only.split(","))
    with open(out, "a") as handle:
        for label, r0, r1, bets, every, cap, timed in SPOTS:
            if only and label not in only:
                continue
            for dtype in ("float32", "float64"):
                row = run(args.gpu, label, r0, r1, bets, every, cap, timed, dtype)
                print(json.dumps(row), flush=True)
                handle.write(json.dumps(row) + "\n")
                handle.flush()


if __name__ == "__main__":
    main()
