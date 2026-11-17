"""Phase 7 comparison suite: solve each spot on both solvers, emit metrics.

Usage: .venv-engine/bin/python -m scripts.phase7.run_suite [spot ...]
Writes scripts/phase7/runs/suite.json incrementally after each spot.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.phase7.spots import SPOTS
from scripts.phase7.ours import solve_ours
from scripts.phase7.theirs import solve_ts
from scripts.phase7.compare import compare_trees, compare_strategies


def _range_weights(spot):
    """{player: {combo_str: weight}} for reach-weighted aggregation."""
    from holdem_solver.protocol.cards import combo_str, parse_range

    return {
        p: {combo_str(c): w for c, w in parse_range(r).items()}
        for p, r in ((0, spot.range0), (1, spot.range1))
    }

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "suite.json")

# tolerances (also written in docs/PHASE7.md before the run)
EV_PCT_TOL = 1.0          # |ΔEV| <= 1% of pot + 0.05 chips
EV_ABS_SLACK = 0.05
FREQ_MEAN_TOL = 0.05      # 5 pp
FREQ_MEAN_REVIEW = 0.15
FREQ_MAX_REVIEW = 0.40
EXPL_TOL = 0.75           # each solver <= 0.75% of pot


def verdict(rec):
    tree_ok = rec["tree"]["ok"]
    ev_ok = abs(rec["ev_diff_chips"]) <= EV_ABS_SLACK + EV_PCT_TOL * rec["pot"] / 100.0
    expl_ok = (
        rec["ours"]["exploitability_pct"] is not None
        and rec["ts"]["exploitability_pct"] is not None
        and rec["ours"]["exploitability_pct"] <= EXPL_TOL
        and rec["ts"]["exploitability_pct"] <= EXPL_TOL
    )
    mean_d = rec["freq"]["mean_abs_diff"]
    max_d = rec["freq"]["max_abs_diff"]
    freq_ok = mean_d <= FREQ_MEAN_TOL
    freq_review = mean_d <= FREQ_MEAN_REVIEW and max_d <= FREQ_MAX_REVIEW
    if tree_ok and ev_ok and freq_ok and expl_ok:
        return "match"
    if ev_ok and expl_ok:
        return "explained"  # EVs agree; freq diffs = non-unique equilibrium or tree deltas
    return "unresolved"


def main(names=None):
    os.makedirs(RUNS, exist_ok=True)
    spots = [s for s in SPOTS if names is None or s.game_name in names]
    results = {}
    if os.path.exists(OUT):
        try:
            results = json.load(open(OUT))
        except Exception:
            results = {}
    for spot in spots:
        name = spot.game_name
        print("=== %s ===" % name, flush=True)
        dump_path = os.path.join(RUNS, "%s_ts.json" % name)
        try:
            ours = solve_ours(spot, dtype="float64", accuracy_pct=0.5)
        except Exception as exc:
            results[name] = {"error_ours": repr(exc)}
            print("  ours failed:", exc, flush=True)
            json.dump(results, open(OUT, "w"), indent=1)
            continue
        print("  ours: ev=%.4f expl=%.3f%% build=%.1fs solve=%.1fs iters=%d"
              % (ours["ev_p0"], ours["exploitability_pct"], ours["build_s"],
                 ours["solve_s"], ours["iterations"]), flush=True)
        try:
            ts = solve_ts(spot, dump_path, threads=8, accuracy_pct=0.5,
                          max_iteration=4000, print_interval=25)
        except Exception as exc:
            results[name] = {
                "ours": {k: v for k, v in ours.items() if k != "nodes"},
                "error_ts": repr(exc),
            }
            print("  ts failed:", exc, flush=True)
            json.dump(results, open(OUT, "w"), indent=1)
            continue
        print("  ts: ev=%.4f expl=%s%% wall=%.1fs iters=%s"
              % (ts["ev_p0"], ts["exploitability_pct"], ts["solve_s"], ts["iterations"]), flush=True)
        tree = compare_trees(ours["nodes"], ts["nodes"], max_depth=3, board=list(spot.board))
        freq = compare_strategies(
            ours["nodes"], ts["nodes"], spot.pot, board=list(spot.board),
            ranges=_range_weights(spot),
        )
        ev_diff = ours["ev_p0"] - ts["ev_p0"]
        rec = {
            "pot": spot.pot,
            "ours": {k: v for k, v in ours.items() if k != "nodes"},
            "ts": {k: v for k, v in ts.items() if k != "nodes"},
            "ev_diff_chips": ev_diff,
            "ev_diff_pct": ev_diff / spot.pot * 100.0,
            "tree": {"ok": tree["ok"], "checked": tree["checked"],
                     "mismatches": tree["mismatches"][:20]},
            "freq": {k: v for k, v in freq.items() if k != "per_node"},
            "per_node": freq["per_node"],
        }
        rec["verdict"] = verdict(rec)
        results[name] = rec
        json.dump(results, open(OUT, "w"), indent=1)
        print("  verdict:", rec["verdict"], flush=True)
    print("wrote", OUT)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
