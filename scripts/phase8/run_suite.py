"""Phase 8 weighted-range suite plus step-3 cross-evaluation.

Usage: .venv-engine/bin/python -m scripts.phase8.run_suite [spot ...]
Writes scripts/phase8/runs/suite8.json incrementally after each spot.

Per spot: solve ours (float64) and TexasSolver, compare trees and
reach-weighted frequencies (same aggregation both sides), evaluate the TS
strategy inside our evaluator (cross-eval), and compute the EV-erased
frequency gap under the TS profile.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.phase7.compare import (
    _amounts_close, _drop_dead_rivers, action_key, compare_strategies,
    compare_trees,
)
from scripts.phase7.grid import _node_weights
from scripts.phase7.ours import solve_ours
from scripts.phase7.texas_dump import canonical_combo, parse_dump
from scripts.phase7.theirs import solve_ts
from scripts.phase8.spots import SPOTS
from scripts.phase8.xeval import _fuzzy_find, node_action_evs, ts_flat_strategy
from scripts.phase7.run_suite import verdict

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "suite8.json")

XEVAL_TOL = 1.0  # TS strategy <= 1.0% of pot in our evaluator (2x 0.5% tol)

# Verified causes for cross-eval failures; a failure with no cause here
# downgrades the verdict to "unresolved".
XEVAL_EXPLANATIONS = {
    "w-turn-cap0": "D4: our tree offers OOP river leads after a called turn "
    "bet; TS's tree does not, so its strategy is exploitable through that "
    "line. Mapped profile value reproduces dump_ev exactly.",
}


def _range_weights(spot):
    from holdem_solver.protocol.cards import combo_str, parse_range

    return {
        p: {combo_str(c): w for c, w in parse_range(r).items()}
        for p, r in ((0, spot.range0), (1, spot.range1))
    }


def cross_eval(spot, dump_path):
    """TS strategy evaluated in our evaluator + ours for reference."""
    import numpy as np

    from holdem_solver.engine_worker.solver import SolverEngine
    from holdem_solver.engine_worker.spot_builder import build_spot

    built = build_spot(spot)
    engine = SolverEngine(built.spec, dtype="float64", device="cuda")
    built.apply_weights(engine._require_solver())
    import torch

    evaluator = engine._get_evaluator()
    solver = engine._require_solver()
    flat_ours = solver.average_strategy_flat()
    ts_nodes = parse_dump(dump_path)
    flat_ts, info = ts_flat_strategy(
        built.spec, ts_nodes, board=list(spot.board),
        key_order=list(solver.average_strategy()),
    )
    flat_ts_t = torch.as_tensor(
        np.asarray(flat_ts, dtype=np.float64),
        dtype=flat_ours.dtype, device=flat_ours.device,
    )
    res_ts = evaluator.evaluate(flat_ts_t)
    out_ts = {
        "exploitability_chips": float(res_ts.exploitability),
        "exploitability_pct": float(res_ts.exploitability) / spot.pot * 100.0,
        "profile_value_p0": float(res_ts.profile_value_p0),
        "mapping": info,
    }
    engine.solve(target_exploitability=0.005 * spot.pot)
    res_ours = evaluator.evaluate(engine._require_solver().average_strategy_flat())
    out_ours = {
        "exploitability_chips": float(res_ours.exploitability),
        "exploitability_pct": float(res_ours.exploitability) / spot.pot * 100.0,
        "profile_value_p0": float(res_ours.profile_value_p0),
        "iterations": engine.iterations,
    }
    engine.close()
    return {"ts_in_ours": out_ts, "ours": out_ours}


def _ts_by_node(ts_nodes):
    by_node = {}
    for node in ts_nodes:
        row = by_node.setdefault((node["player"], tuple(node["history"])), {})
        for combo, freqs in node.get("strategy", {}).items():
            row[combo] = freqs
    return by_node


def ev_gap(spot, dump_path, ours_nodes):
    """Reach-weighted mean per-combo |Δf| vs |Σ_a Δσ·EV_a| under the TS
    profile — EVs from ``node_action_evs``. Both means over the same
    (node, combo, weight) set. River spots only."""
    if len(spot.board) != 5:
        return {"skipped": "turn spot — EV gap computed on river spots only"}
    ts_nodes = _drop_dead_rivers(parse_dump(dump_path), list(spot.board))
    ts_idx = {}
    for n in ts_nodes:
        sig = tuple(action_key(h)[0] for h in n["history"])
        ts_idx.setdefault((n["player"], sig), []).append(n)
    by_node = _ts_by_node(ts_nodes)
    nodes_evs = node_action_evs(
        dump_path, spot.pot, list(spot.board), spot.range0, spot.range1
    )
    ranges = _range_weights(spot)

    total_fd = total_eg = total_w = 0.0
    shared = 0
    for node in ours_nodes:
        who = node["player"]
        hist = tuple(node["history"])
        ts_node = _fuzzy_find(ts_idx, who, list(hist))
        if ts_node is None:
            continue
        ev_entry = nodes_evs.get((who, hist))
        if ev_entry is None:
            continue
        blocked_now = set(spot.board) | {h[6:] for h in hist if h.startswith("river:")}
        weights = _node_weights(
            by_node, who, hist, ranges.get(who, {}), ranges.get(1 - who, {}),
            blocked_now,
        )
        ts_strat = ts_node.get("strategy", {})
        for combo, freqs_ours in node.get("strategy", {}).items():
            w = weights.get(canonical_combo(combo), 0.0)
            if w <= 0.0:
                continue
            freqs_ts = ts_strat.get(canonical_combo(combo))
            if freqs_ts is None:
                continue
            evs = ev_entry["evs"].get(canonical_combo(combo))
            if evs is None:
                continue
            fd_sum = 0.0
            eg_sum = 0.0
            nact = 0
            for a, of in freqs_ours.items():
                ka, aa = action_key(a)
                tf = None
                ev_a = None
                for raw, f in freqs_ts.items():
                    kb, ab = action_key(raw)
                    if kb == ka and _amounts_close(aa, ab):
                        tf = f
                        break
                if tf is None:
                    continue
                for raw, ev in evs.items():
                    kb, ab = action_key(raw)
                    if kb == ka and _amounts_close(aa, ab):
                        ev_a = ev
                        break
                nact += 1
                fd_sum += abs(of - tf)
                if ev_a is not None:
                    eg_sum += (of - tf) * ev_a
            if nact:
                total_fd += w * fd_sum / nact
                total_eg += w * abs(eg_sum)
                total_w += w
                shared += 1
    if total_w <= 0:
        return {"skipped": "no shared weighted combos"}
    return {
        "combos": shared,
        "weighted_freq_diff_pp": total_fd / total_w * 100.0,
        "ev_gap_pct_pot": total_eg / total_w / spot.pot * 100.0,
    }


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
        print("  ours: ev=%.4f expl=%.3f%% iters=%d"
              % (ours["ev_p0"], ours["exploitability_pct"], ours["iterations"]),
              flush=True)
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
                # A failed cross-eval can only worsen the verdict: never
                # overwrite "unresolved" with "explained".
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
