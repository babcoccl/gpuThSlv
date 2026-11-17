"""Phase 9 step 6: subtree-cache gate + build-time report.

The build-one-deal/copy-per-deal mechanism shipped in Phase 6; this script
verifies it still holds the recorded invariants:

1. cap-0 consistency — for each Phase 8 river raise_cap=0 spot, re-solve
   ours, compare the tree against the saved TS dump (compare_trees), and
   require the node count to equal the number recorded in suite8.json;
2. equal-tree reruns — same check on the Phase 8.6 equal-tree spots vs
   suite8_6.json;
3. build time — time ``build_river_spec`` on the 10.37M-node raise spot
   (river-full-pot cap 2); Phase 6 recorded 60.7 s.

Runs in ``.venv-engine``. Usage:
.venv-engine/bin/python -m scripts.phase9.tree_gate [--skip-solves]
Writes scripts/phase9/runs/tree_gate.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.spot import SpotConfig
from scripts.phase7.compare import compare_trees
from scripts.phase7.ours import solve_ours
from scripts.phase7.texas_dump import parse_dump
from scripts.phase8.spots import SPOTS as SPOTS8
from scripts.phase8_6.spots import SPOTS as SPOTS86

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "tree_gate.json")
SUITE8 = os.path.join(os.path.dirname(__file__), "..", "phase8", "runs")
SUITE86 = os.path.join(os.path.dirname(__file__), "..", "phase8_6", "runs")

CAP0_RIVER = [s for s in SPOTS8 if s.raise_cap == 0 and len(s.board) == 5]

RAISE_SPOT = SpotConfig(
    "KsJsTh7d2c", "*", "22+, A2+, K2+, Q2+",
    pot=20, stack=100, bet_sizes="half", raise_cap=2,
    raise_sizes=(("p", 1.0),), max_nodes=None,
)
PHASE6_SPEC_BUILD_S = 60.7  # recorded in docs/PHASE6.md
PHASE6_NODES = 10_374_211


def check_equal(spot, dump_name, suite_name, recorded):
    """solve_ours -> compare_trees vs saved dump -> node count match."""
    dump_path = os.path.join(suite_name, "%s_ts.json" % dump_name)
    ours = solve_ours(spot, dtype="float64", accuracy_pct=0.5)
    ts_nodes = parse_dump(dump_path)
    tree = compare_trees(ours["nodes"], ts_nodes, board=list(spot.board))
    rec_nodes = (recorded.get("ours") or {}).get("num_tree_nodes")
    got_nodes = ours.get("num_tree_nodes")
    ok = bool(tree.get("ok")) and (rec_nodes is None or got_nodes == rec_nodes)
    return {
        "nodes_ours": got_nodes,
        "nodes_recorded": rec_nodes,
        "tree_ok": bool(tree.get("ok")),
        "checked": tree.get("checked"),
        "mismatches": tree.get("mismatches") or [],
        "ok": ok,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-solves", action="store_true",
                        help="only time the spec build (no GPU solves)")
    args = parser.parse_args()
    os.makedirs(RUNS, exist_ok=True)
    results = {"spots": {}}

    suite8 = json.load(open(os.path.join(SUITE8, "suite8.json"))) \
        if not args.skip_solves else {}
    suite86 = json.load(open(os.path.join(SUITE86, "suite8_6.json"))) \
        if not args.skip_solves else {}

    if not args.skip_solves:
        for spot in CAP0_RIVER:
            name = spot.name
            print("=== cap0 %s ===" % name, flush=True)
            recorded = suite8.get(name) or {}
            out = check_equal(spot, name, SUITE8, recorded)
            print("  nodes=%s recorded=%s tree_ok=%s"
                  % (out["nodes_ours"], out["nodes_recorded"], out["tree_ok"]),
                  flush=True)
            results["spots"][name] = out
        for spot, _emit_donk, _x in SPOTS86:
            name = spot.game_name
            print("=== phase8.6 %s ===" % name, flush=True)
            recorded = suite86.get(name) or {}
            out = check_equal(spot, name, SUITE86, recorded)
            print("  nodes=%s recorded=%s tree_ok=%s"
                  % (out["nodes_ours"], out["nodes_recorded"], out["tree_ok"]),
                  flush=True)
            results["spots"][name] = out

    # --- build time on the 10.37M-node raise spot ---------------------------
    from holdem_solver.engine_worker.tree_builder import build_river_spec

    resolved = RAISE_SPOT.resolve()
    hands0 = [c for c, _w in resolved.hands0]
    hands1 = [c for c, _w in resolved.hands1]
    t0 = time.perf_counter()
    spec = build_river_spec(
        RAISE_SPOT.board, RAISE_SPOT.pot, RAISE_SPOT.stack,
        resolved.bet_fractions, hands0, hands1,
        raise_cap=RAISE_SPOT.raise_cap, raise_sizes=RAISE_SPOT.raise_sizes,
        name="phase9-build",
    )
    build_s = time.perf_counter() - t0
    num_nodes = len(spec.nodes)
    results["build"] = {
        "spot": "river-full-pot cap 2",
        "spec_build_s": build_s,
        "phase6_spec_build_s": PHASE6_SPEC_BUILD_S,
        "num_nodes": num_nodes,
        "phase6_num_nodes": PHASE6_NODES,
        "nodes_match": num_nodes == PHASE6_NODES,
    }
    print("build: %.2f s (phase6 %.2f s), nodes=%d (phase6 %d)"
          % (build_s, PHASE6_SPEC_BUILD_S, num_nodes, PHASE6_NODES), flush=True)

    results["ok"] = all(s.get("ok", True) for s in results["spots"].values())
    json.dump(results, open(OUT, "w"), indent=1)
    print("written to %s" % OUT)
    sys.exit(0 if results["ok"] else 1)


if __name__ == "__main__":
    main()
