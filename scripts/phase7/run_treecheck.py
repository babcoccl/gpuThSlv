"""Tree equivalence check: build both trees, compare action sets depth<=3.

Runs under .venv-engine (imports ours) but drives TS via subprocess.
Usage: .venv-engine/bin/python -m scripts.phase7.run_treecheck [spot ...]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.phase7.spots import SPOTS
from scripts.phase7.ours import tree_nodes
from scripts.phase7.theirs import solve_ts
from scripts.phase7.compare import compare_trees

RUNS = os.path.join(os.path.dirname(__file__), "runs")


def main(names=None):
    os.makedirs(RUNS, exist_ok=True)
    spots = [s for s in SPOTS if names is None or s.game_name in names]
    report = {}
    for spot in spots:
        dump_path = os.path.join(RUNS, "%s_ts.json" % spot.game_name)
        ours, total = tree_nodes(spot)
        ts = solve_ts(spot, dump_path, threads=8, accuracy_pct=50.0,
                      max_iteration=2, print_interval=1)
        cmp = compare_trees(ours, ts["nodes"], max_depth=3, board=list(spot.board))
        report[spot.game_name] = {
            "ours_nodes": total,
            "ts_nodes": len(ts["nodes"]),
            "checked": cmp["checked"],
            "mismatches": cmp["mismatches"],
            "ts_exit": ts["exit_code"],
        }
        print("== %s: ours=%d ts=%d checked=%d mismatches=%d"
              % (spot.game_name, total, len(ts["nodes"]), cmp["checked"], len(cmp["mismatches"])))
        for m in cmp["mismatches"][:8]:
            print("   ", json.dumps(m))
    out = os.path.join(RUNS, "treecheck.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
