"""Phase 9 EV validation: /node EV machinery vs the Phase 7 dump_ev walk.

Runs in ``.venv-engine``. For each of the 6 river raise_cap=0 spots:

1. root EV equality — ``evwalk.root_ev`` on the TexasSolver dump's tree
   equals ``dump_ev`` (the TS-side reference walk) within the standing
   tolerance (|ΔEV| <= 1.0% pot + 0.05 chips);
2. per-node EV equality — ``evwalk.node_action_evs`` equals
   ``xeval.node_action_evs`` (the Phase 8 dump-walk reference) at every
   action node, per combo per action;
3. status-value equality — ``evwalk.root_ev`` on our own solved strategy
   equals ``profile_value_p0`` (the value the API status reports).

The saved Phase 8 TS dumps are reused (deterministic inputs); our solves
run fresh on the GPU.

Usage: .venv-engine/bin/python -m scripts.phase9.validate_node_evs [spot ...]
Writes scripts/phase9/runs/ev_validation.json.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.cards import combo_str, parse_cards, parse_range
from holdem_solver.protocol.evwalk import (
    TreeIndex, dealt_board, initial_reaches, node_action_evs as our_action_evs,
    reach_at, root_ev,
)
from scripts.phase7.ours import solve_ours
from scripts.phase7.texas_dump import parse_dump
from scripts.phase8.spots import SPOTS
from scripts.phase8.xeval import node_action_evs as ts_action_evs

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "ev_validation.json")
TS_DUMPS = os.path.join(os.path.dirname(__file__), "..", "phase8", "runs")

CAP0_RIVER = [s for s in SPOTS if s.raise_cap == 0 and len(s.board) == 5]

TOL_FRAC_OF_POT = 0.01   # standing tolerance: 1.0% of pot
TOL_CHIPS = 0.05         #   + 0.05 chips absolute


def tol(pot):
    return TOL_FRAC_OF_POT * pot + TOL_CHIPS


def ts_index(nodes):
    """TreeIndex from parse_dump output."""
    by_node = {}
    for node in nodes:
        hist = tuple(node["history"])
        by_node.setdefault((node["player"], hist), {}).update(node["strategy"])
    return TreeIndex(by_node)


def our_index(nodes):
    """TreeIndex from solve_ours output (same node shape)."""
    return ts_index(nodes)


def main(names=None):
    os.makedirs(RUNS, exist_ok=True)
    results = {}
    for spot in CAP0_RIVER:
        if names is not None and spot.name not in names:
            continue
        name = spot.name
        print("=== %s ===" % name, flush=True)
        dump_path = os.path.join(TS_DUMPS, "%s_ts.json" % name)
        pot = spot.pot
        board_ids = tuple(parse_cards(spot.board))
        dead_ids = tuple(parse_cards(spot.dead_cards or ()))
        blocked = board_ids + dead_ids
        w0 = {combo_str(c): w for c, w in parse_range(spot.range0).items()}
        w1 = {combo_str(c): w for c, w in parse_range(spot.range1).items()}

        # --- 1. root EV on the TS dump's tree -------------------------------
        from scripts.phase7.texas_dump import dump_ev

        ts_nodes = parse_dump(dump_path)
        index = ts_index(ts_nodes)
        our_root = root_ev(index, w0, w1, board_ids, dead_ids, pot)
        ref_root = dump_ev(dump_path, pot=pot, board=list(spot.board),
                           range0=spot.range0, range1=spot.range1)
        root_diff = abs(our_root - ref_root)
        print("  ts-tree root ev: ours=%.6f dump_ev=%.6f |d|=%.6f (tol %.3f)"
              % (our_root, ref_root, root_diff, tol(pot)), flush=True)

        # --- 2. per-node per-combo per-action EVs on the TS tree ------------
        ref_nodes = ts_action_evs(
            dump_path, pot=pot, board=list(spot.board),
            range0=spot.range0, range1=spot.range1, max_combos=10_000,
        )
        compared = 0
        max_diff = 0.0
        worst = None
        for (player, hist), ref in sorted(ref_nodes.items()):
            hist = tuple(hist)
            if index.edge_kind(hist) != "player":
                continue
            reach0, reach1 = reach_at(index, hist, w0, w1, blocked)
            board_now = dealt_board(spot.board, hist)
            ours = our_action_evs(index, hist, reach0, reach1, board_now, pot)
            for combo, ref_evs in ref["evs"].items():
                if combo not in ours:
                    continue
                for action, ref_ev in ref_evs.items():
                    if ref_ev is None:
                        continue
                    got = ours[combo].get(action)
                    if got is None:
                        continue
                    d = abs(got - ref_ev)
                    compared += 1
                    if d > max_diff:
                        max_diff = d
                        worst = (player, hist, combo, action)
        print("  node EVs: %d compared, max |d|=%.6f (tol %.3f) worst=%s"
              % (compared, max_diff, tol(pot), worst), flush=True)

        # --- 3. our solve's root EV vs profile_value_p0 --------------------
        ours = solve_ours(spot, dtype="float64", accuracy_pct=0.5)
        own_index = our_index(ours["nodes"])
        own_root = root_ev(own_index, w0, w1, board_ids, dead_ids, pot)
        status_diff = abs(own_root - ours["ev_p0"])
        print("  own root ev: evwalk=%.6f profile_value_p0=%.6f |d|=%.6f"
              % (own_root, ours["ev_p0"], status_diff), flush=True)

        limit = tol(pot)
        results[name] = {
            "pot": pot,
            "tol_chips": limit,
            "root_ev_ts_tree": {"evwalk": our_root, "dump_ev": ref_root,
                               "abs_diff": root_diff, "ok": root_diff <= limit},
            "node_evs": {"compared": compared, "max_abs_diff": max_diff,
                         "ok": max_diff <= limit and compared > 0},
            "root_ev_own_tree": {"evwalk": own_root,
                                 "profile_value_p0": ours["ev_p0"],
                                 "abs_diff": status_diff,
                                 "ok": status_diff <= limit},
            "ok": root_diff <= limit and max_diff <= limit
                  and status_diff <= limit and compared > 0,
        }
        json.dump(results, open(OUT, "w"), indent=1)

    n_ok = sum(1 for r in results.values() if r["ok"])
    print("\n%d/%d spots within tolerance" % (n_ok, len(results)))
    print("written to %s" % OUT)
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
