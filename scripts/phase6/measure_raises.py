#!/usr/bin/env python
"""Phase 6 step 2: measure custom-builder trees at raise caps 0, 1, 2.

Runs in ``.venv-engine``. For each spot x raise cap: builds the spec with
``holdem_solver.engine_worker.tree_builder.build_river_spec`` (timed), builds
a ``StagewiseCFRSolver``, runs CFR+ iterations (timed), and records node
count, infoset count, peak reserved/allocated VRAM, and it/s.

    .venv-engine/bin/python scripts/phase6/measure_raises.py [spot ...] [--iters N]
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "third_party" / "GPU-CFR"))
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

RIVER = "KsJsTh7d2c"
BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo"
RAISE_SIZES = (("p", 1.0),)  # pot-sized raise only


def spots():
    from holdem_solver.protocol.spot import SpotConfig

    return [
        ("river-toy", SpotConfig(RIVER, "AhQh, 4c3c, 4d3d, 4h3h", "9d9h",
                                 pot=10, stack=10, bet_sizes="pot")),
        ("river-narrow", SpotConfig(RIVER, "77-TT", "22-66, A2s-A5s",
                                    pot=20, stack=100, bet_sizes="standard")),
        ("river-broad-standard", SpotConfig(RIVER, BROAD, BROAD,
                                            pot=20, stack=100, bet_sizes="standard")),
        ("river-broad-wide-allin", SpotConfig(RIVER, BROAD, BROAD,
                                              pot=20, stack=100, bet_sizes="wide",
                                              all_in=True)),
        ("river-full-pot", SpotConfig(RIVER, "*", "22+, A2+, K2+, Q2+",
                                      pot=20, stack=100, bet_sizes="half",
                                      max_nodes=None)),
    ]


def measure(config, raise_cap, iters):
    import torch
    from gpu_cfr.config import CFRConfig, DeviceConfig
    from gpu_cfr.solvers.stagewise import StagewiseCFRSolver
    from holdem_solver.engine_worker.tree_builder import build_river_spec

    resolved = config.resolve()
    hands0 = [c for c, _ in resolved.hands0]
    hands1 = [c for c, _ in resolved.hands1]
    t0 = time.perf_counter()
    spec = build_river_spec(config.board, config.pot, config.stack,
                            resolved.bet_fractions, hands0, hands1,
                            raise_cap=raise_cap, raise_sizes=RAISE_SIZES,
                            name="measure")
    spec_build_s = time.perf_counter() - t0
    num_infoset_actions = sum(len(i.actions) for i in spec.infosets.values())

    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    cfg = CFRConfig(game=spec.name, variant="cfr_plus", iterations=iters,
                    seed=42, device=DeviceConfig(device="cuda", dtype="float32"),
                    cuda_graph=False)
    solver = StagewiseCFRSolver(spec, cfg)
    solver_build_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    solver.step(iters)
    torch.cuda.synchronize()
    solve_s = time.perf_counter() - t0
    row = {
        "nodes": len(spec.nodes),
        "infosets": len(spec.infosets),
        "infoset_actions": num_infoset_actions,
        "num_deals": resolved.num_deals,
        "raise_cap": raise_cap,
        "spec_build_s": round(spec_build_s, 3),
        "solver_build_s": round(solver_build_s, 3),
        "iters": iters,
        "solve_s": round(solve_s, 3),
        "it_per_s": round(iters / solve_s, 1),
        "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
    }
    del solver, spec
    torch.cuda.empty_cache()
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("spots", nargs="*")
    parser.add_argument("--iters", type=int, default=200)
    parser.add_argument("--caps", type=int, nargs="*", default=[0, 1, 2])
    parser.add_argument("--out", default=str(ROOT / "scripts/phase6/logs/raise_measurements.json"))
    args = parser.parse_args()

    wanted = set(args.spots)
    rows = []
    for name, config in spots():
        if wanted and name not in wanted:
            continue
        for cap in args.caps:
            try:
                row = measure(config, cap, args.iters)
            except Exception as exc:
                row = {"raise_cap": cap, "error": "%s: %s" % (type(exc).__name__, exc)}
            row["spot"] = name
            print(json.dumps(row), flush=True)
            rows.append(row)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
