"""Phase 9/9.1 EV validation: /node EV machinery vs the Phase 7 dump_ev walk.

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

Evidence integrity (Phase 9.1): every run writes a DISTINCT artifact —
``runs/ev_validation_<spot>+<spot>+....json`` — whose filename carries the
spot list and whose ``_manifest`` records the spots, per-spot comparison
counts, the strategy dtype/source of each comparison, and the git commit.
A partial run can never overwrite the full 6-spot artifact.

Usage: .venv-engine/bin/python -m scripts.phase9.validate_node_evs [spot ...]

Import boundary (Phase 10.4): this engine stage runs only the checks that
need the engine runtime — the dump walks (steps 1–2) and the fresh GPU
solve (step 3) — and never imports ``holdem_solver.api``. It writes the
solved tree to ``runs/solved_nodes_<spot>.json``; the API-path checks
(evec, persisted, served) run in the API venv via
``scripts.phase9.validate_node_evs_api``, which merges its results into
the same ``ev_validation_<spots>.json`` artifact under
``_manifest.stages.api``.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.cards import combo_str, parse_cards, parse_range
from holdem_solver.protocol.evwalk import (
    TreeIndex, dealt_board, node_action_evs as our_action_evs,
    reach_at, root_ev,
)
from scripts.phase7.ours import solve_ours
from scripts.phase7.texas_dump import parse_dump
from scripts.phase8.spots import SPOTS
from scripts.phase8.xeval import node_action_evs as ts_action_evs

RUNS = os.path.join(os.path.dirname(__file__), "runs")
TS_DUMPS = os.path.join(os.path.dirname(__file__), "..", "phase8", "runs")

CAP0_RIVER = [s for s in SPOTS if s.raise_cap == 0 and len(s.board) == 5]
FULL_RUN = [s.name for s in CAP0_RIVER]

TOL_FRAC_OF_POT = 0.01   # standing tolerance: 1.0% of pot
TOL_CHIPS = 0.05         #   + 0.05 chips absolute

DTYPE = "float64"  # solve_ours dtype used for every comparison in this file


def out_path(names):
    """Distinct artifact per spot set: the filename carries the spot list."""
    return os.path.join(RUNS, "ev_validation_%s.json" % "+".join(names))


def partial_path(names):
    """In-progress output; renamed to ``out_path`` only on completion, so an
    interrupted run can never masquerade as the finished artifact."""
    return out_path(names) + ".partial"


def tol(pot):
    return TOL_FRAC_OF_POT * pot + TOL_CHIPS


def git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        ).decode().strip()
    except Exception:
        return "unknown"


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


def solved_nodes_path(name):
    """Intermediate the API stage consumes: the solved tree the engine
    wrote for ``name``."""
    return os.path.join(RUNS, "solved_nodes_%s.json" % name)


def main(names=None):
    os.makedirs(RUNS, exist_ok=True)
    spots = [s for s in CAP0_RIVER if names is None or s.name in names]
    if names is not None:
        missing = [n for n in names if n not in {s.name for s in spots}]
        if missing:
            print("unknown spot(s): %s" % ", ".join(missing))
            sys.exit(2)
    run_names = [s.name for s in spots]
    out = out_path(run_names)
    results = {}
    for spot in spots:
        name = spot.name
        print("=== %s ===" % name, flush=True)
        dump_path = os.path.join(TS_DUMPS, "%s_ts.json" % name)
        pot = spot.pot
        board_ids = tuple(parse_cards(spot.board))
        dead_ids = tuple(parse_cards(spot.dead_cards or ()))
        blocked = board_ids + dead_ids
        w0 = {combo_str(c): w for c, w in parse_range(spot.range0).items()}
        w1 = {combo_str(c): w for c, w in parse_range(spot.range1).items()}
        dump_rel = os.path.relpath(dump_path, os.path.join(os.path.dirname(__file__), "..", ".."))

        # --- 1. root EV on the TS dump's tree -------------------------------
        from scripts.phase7.texas_dump import dump_ev

        ts_nodes = parse_dump(dump_path)
        index = ts_index(ts_nodes)
        our_root = root_ev(index, w0, w1, board_ids, dead_ids, pot)
        ref_root = dump_ev(dump_path, pot=pot, board=list(spot.board),
                           range0=spot.range0, range1=spot.range1)
        root_diff = abs(our_root - ref_root)
        print("  ts-tree root ev: evwalk=%r dump_ev=%r |d|=%r (tol %.3f)"
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
        print("  node EVs: %d compared, max |d|=%r (tol %.3f) worst=%s"
              % (compared, max_diff, tol(pot), worst), flush=True)

        # --- 3. our solve's root EV vs profile_value_p0 --------------------
        ours = solve_ours(spot, dtype=DTYPE, accuracy_pct=0.5)
        own_index = our_index(ours["nodes"])
        own_root = root_ev(own_index, w0, w1, board_ids, dead_ids, pot)
        status_diff = abs(own_root - ours["ev_p0"])
        print("  own root ev: evwalk=%r profile_value_p0=%r |d|=%r"
              % (own_root, ours["ev_p0"], status_diff), flush=True)
        # hand the solved tree to the API stage (api.evec/persisted checks
        # live there — engine-venv code never imports holdem_solver.api)
        json.dump(
            {"spot": spot.to_dict(), "nodes": ours["nodes"],
             "ev_p0": ours["ev_p0"], "dtype": DTYPE},
            open(solved_nodes_path(name), "w"),
        )

        limit = tol(pot)
        results[name] = {
            "pot": pot,
            "tol_chips": limit,
            "root_ev_ts_tree": {
                "evwalk": our_root, "dump_ev": ref_root,
                "abs_diff": root_diff, "ok": root_diff <= limit,
                "strategy_source": "TexasSolver dump_result average strategy (%s)" % dump_rel,
                "dtype": "float64 (dump JSON doubles; walk in Python float)"},
            "node_evs": {
                "compared": compared, "max_abs_diff": max_diff,
                "ok": max_diff <= limit and compared > 0,
                "strategy_source": "TexasSolver dump_result average strategy (%s)" % dump_rel,
                "dtype": "float64 (dump JSON doubles; walk in Python float)"},
            "root_ev_own_tree": {
                "evwalk": own_root, "profile_value_p0": ours["ev_p0"],
                "abs_diff": status_diff, "ok": status_diff <= limit,
                "strategy_source": "gpu_cfr average strategy (solve_ours, fresh solve)",
                "dtype": DTYPE},
            "ok": root_diff <= limit and max_diff <= limit
                  and status_diff <= limit and compared > 0,
        }
        _write(partial_path(run_names), results, requested=run_names)

    n_ok = sum(1 for r in results.values() if r["ok"])
    _write(out, results, requested=run_names, complete=True)
    if os.path.exists(partial_path(run_names)):
        os.remove(partial_path(run_names))
    print("\n%d/%d spots within tolerance" % (n_ok, len(results)))
    print("written to %s" % out)
    sys.exit(0 if n_ok == len(results) and len(results) == len(spots) else 1)


def _write(out, results, requested, complete=False):
    run_names = list(results)
    manifest = {
        "spots": run_names,
        "requested_spots": requested,
        "complete": complete,
        "spot_count": len(run_names),
        "is_full_run": sorted(run_names) == sorted(FULL_RUN),
        "expected_full_run": FULL_RUN,
        "git_commit": git_commit(),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stages": {
            "engine": {
                "git_commit": git_commit(),
                "ran_at": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(),
            },
        },
        "comparisons": {
            name: {
                "node_evs_compared": r["node_evs"]["compared"],
                "node_evs_max_abs_diff": r["node_evs"]["max_abs_diff"],
            }
            for name, r in results.items()
        },
        "total_node_evs_compared": sum(
            r["node_evs"]["compared"] for r in results.values()
        ),
        "max_abs_diff": max(
            (r["node_evs"]["max_abs_diff"] for r in results.values()), default=0.0
        ),
    }
    json.dump({"_manifest": manifest, "spots": results}, open(out, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
