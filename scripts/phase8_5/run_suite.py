"""Phase 8.5 suite: donk turn re-runs, per-player/per-street sizes, raises.

Usage: .venv-engine/bin/python -m scripts.phase8_5.run_suite [spot ...]
Writes scripts/phase8_5/runs/suite8_5.json incrementally after each spot.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.phase7.compare import compare_strategies, compare_trees
from scripts.phase7.ours import solve_ours
from scripts.phase7.theirs import solve_ts
from scripts.phase7.run_suite import verdict
from scripts.phase8.run_suite import cross_eval, ev_gap, _range_weights, XEVAL_TOL
from scripts.phase8_5.spots import SPOTS

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "suite8_5.json")

XEVAL_EXPLANATIONS = {
    "turn-narrow-a": "D2-ext: TS counts raise actions per street "
    "(raise_limit=4 including the opening bet, shared across players) "
    "while ours caps raises per street; at cap 1 "
    "TS keeps a re-raise line (b5 -> r17 -> r30) that our tree removes, so "
    "its frequencies at shared nodes price in a line that cannot exist for "
    "us and are exploitable. EVs agree (0.26% of pot). Confirmed in "
    "Phase 8.6: at cap 3 (equal trees) xeval is 0.041%.",
    "w-turn-cap0": "D4 resolved in Phase 8.5: donk sizes are emitted so the "
    "trees now match; a residual failure would mean something else.",
}


def main(names=None):
    os.makedirs(RUNS, exist_ok=True)
    entries = [e for e in SPOTS if names is None or e[0].game_name in names]
    results = {}
    if os.path.exists(OUT):
        try:
            results = json.load(open(OUT))
        except Exception:
            results = {}
    for spot, emit_donk, x_pcts in entries:
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
        print("  ours: ev=%.4f expl=%.3f%% iters=%d"
              % (ours["ev_p0"], ours["exploitability_pct"], ours["iterations"]),
              flush=True)
        try:
            ts = solve_ts(spot, dump_path, threads=8, accuracy_pct=0.5,
                          max_iteration=4000, print_interval=25,
                          emit_donk=emit_donk, x_raise_pcts=x_pcts)
        except Exception as exc:
            results[name] = {
                "ours": {k: v for k, v in ours.items() if k != "nodes"},
                "error_ts": repr(exc),
            }
            print("  ts failed:", exc, flush=True)
            json.dump(results, open(OUT, "w"), indent=1)
            continue
        print("  ts: ev=%.4f expl=%s%% iters=%s"
              % (ts["ev_p0"], ts["exploitability_pct"], ts["iterations"]),
              flush=True)
        tree = compare_trees(ours["nodes"], ts["nodes"], max_depth=3,
                             board=list(spot.board))
        freq = compare_strategies(
            ours["nodes"], ts["nodes"], spot.pot, board=list(spot.board),
            ranges=_range_weights(spot),
        )
        ev_diff = ours["ev_p0"] - ts["ev_p0"]
        rec = {
            "pot": spot.pot,
            "emit_donk": emit_donk,
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
        try:
            rec["xeval"] = cross_eval(spot, dump_path)
            rec["xeval_pass"] = (
                rec["xeval"]["ts_in_ours"]["exploitability_pct"] <= XEVAL_TOL
            )
            if not rec["xeval_pass"]:
                cause = XEVAL_EXPLANATIONS.get(name)
                if cause is not None and rec["verdict"] == "match":
                    rec["verdict"] = "explained"
                    rec["verdict_cause"] = cause
                elif rec["verdict"] != "unresolved":
                    rec["verdict"] = "unresolved"
            print("  xeval: ts-in-ours=%.3f%% ours=%.3f%%"
                  % (rec["xeval"]["ts_in_ours"]["exploitability_pct"],
                     rec["xeval"]["ours"]["exploitability_pct"]), flush=True)
        except Exception as exc:
            rec["xeval_error"] = repr(exc)
            print("  xeval failed:", exc, flush=True)
        try:
            rec["ev_gap"] = ev_gap(spot, dump_path, ours["nodes"])
            print("  ev_gap:", rec["ev_gap"], flush=True)
        except Exception as exc:
            rec["ev_gap_error"] = repr(exc)
            print("  ev_gap failed:", exc, flush=True)
        results[name] = rec
        json.dump(results, open(OUT, "w"), indent=1)
        print("  verdict:", rec["verdict"], flush=True)
    print("wrote", OUT)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
