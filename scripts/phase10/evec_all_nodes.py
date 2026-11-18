"""Phase 10.3: ``evec`` vs ``evwalk`` on EVERY node of the persisted scale jobs.

Runs in ``.venv-api`` (numpy). For each persisted solve listed in
``logs/scale-10-2.json``:

1. **Aggregate check at every history.** For every position ``hist`` in the
   tree — player nodes, chance positions, and terminal edges — the pure-
   Python ``evwalk.edge_value`` (one full subtree walk per hist) is compared
   against ``Σ_c reach_own[c]·V[c]`` where ``V = evec._evs_walk(...)`` is the
   vectorized payoff vector for ``own ∈ {0, 1}``. Tolerance 1e-9. Chance
   positions are covered because ``edge_kind == "chance"`` hists are in the
   set, and each ``river:<card>`` child hist is its own entry.

2. **Persisted vs live evec at every player node.** The committed
   ``strategy-<id>.evs.npz`` (as served by ``/node``) is compared per combo
   per action against a fresh ``export_node_evs`` pass — stale or wrong
   values would surface here.

3. **Exact per-combo check.** ``evec.node_action_evs`` vs
   ``evwalk.node_action_evs`` vs the persisted table, per combo per action,
   at every player hist whose subtree fits a per-job budget
   (``MAX_DESC_PER_NODE`` descendants). Smaller hists — the overwhelming
   majority, including every node adjacent to a chance edge — are all
   checked; larger hists are covered by check 1 plus a parity-split variant
   (two more ``edge_value`` runs with the own reach split in half by combo
   gid, pinning both halves of ``V`` separately).

Writes ``logs/evec_all_nodes.json``; exits 1 on any |delta| > 1e-9.

Usage: .venv-api/bin/python -m scripts.phase10.evec_all_nodes [job_substr ...]
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.cards import card_str  # noqa: E402
from holdem_solver.protocol.evwalk import (  # noqa: E402
    TreeIndex,
    dealt_board,
    edge_value,
    node_action_evs as ref_action_evs,
    reach_at,
    replay_chips,
)
from holdem_solver.api import evec  # noqa: E402
from holdem_solver.api.nodes import (  # noqa: E402
    export_node_evs,
    load_index,
    load_node_evs,
)
from holdem_solver.api.strategy import spot_ranges  # noqa: E402

HERE = os.path.dirname(__file__)
DATA_DIR = os.path.expanduser("~/.local/share/holdem-solver")
DB = os.path.join(DATA_DIR, "solves.db")
SCALE_LOG = os.path.join(HERE, "logs", "scale-10-2.json")
OUT = os.path.join(HERE, "logs", "evec_all_nodes.json")

TOL = 1e-9
# Per-combo exact check is skipped for hists with more descendants than this;
# those are covered by the aggregate + parity checks instead.
MAX_DESC_PER_NODE = 400


def hist_set(index: TreeIndex) -> set:
    """Every position in the tree: player nodes, chance positions, and the
    hist at every edge (terminal edges included)."""
    hists = set(index.node_at) | set(index.chance_cards)
    for h, (_p, acts) in index.node_at.items():
        for a in acts:
            hists.add(h + (a,))
    for h, cards in index.chance_cards.items():
        for c in cards:
            hists.add(h + ("river:" + card_str(c),))
    return hists


def reach_vec(reach: dict) -> np.ndarray:
    v = np.zeros(evec._N)
    for c, r in reach.items():
        v[evec._gid(c)] = r
    return v


def main(names=None):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    jobs = json.load(open(SCALE_LOG))
    if names:
        jobs = [j for j in jobs if any(n in j["name"] or n in j["id"] for n in names)]
        if not jobs:
            print("no matching jobs")
            sys.exit(2)
    db = sqlite3.connect(DB)
    report = {}
    failures = 0
    for job in jobs:
        jid = job["id"]
        name = job["name"]
        print("=== %s (%s) ===" % (name, jid), flush=True)
        t0 = time.time()
        strat = os.path.join(DATA_DIR, "strategy-%s.npz" % jid)
        evs_path = os.path.join(DATA_DIR, "strategy-%s.evs.npz" % jid)
        index, _it = load_index(strat)
        keys = index.keys_list  # type: ignore[attr-defined]
        spot = json.loads(db.execute(
            "select spot from jobs where id = ?", (jid,)).fetchone()[0])
        ranges = spot_ranges(spot)
        board0 = tuple(
            __import__("holdem_solver.protocol.cards", fromlist=["parse_cards"])
            .parse_cards(spot.get("board") or ())
        )
        blocked = board0 + tuple(
            __import__("holdem_solver.protocol.cards", fromlist=["parse_cards"])
            .parse_cards(spot.get("dead_cards") or ())
        )
        pot = float(spot.get("pot", 20.0))
        persisted, masses_p = load_node_evs(evs_path, keys) or ({}, {})
        evs_live, masses_live = export_node_evs(index, spot)

        hists = sorted(hist_set(index))
        n_player = sum(1 for h in hists if index.edge_kind(h) == "player")
        n_chance = sum(1 for h in hists if index.edge_kind(h) == "chance")

        # --- 1+3: aggregate at every hist; exact per-combo under budget ----
        agg_max = 0.0
        split_max = 0.0
        exact_checked = exact_max = 0.0
        exact_cells = 0
        exact_hists = 0
        persisted_checked = persisted_max = 0.0
        persist_cells = 0
        exact_budget_left = None  # all player hists under MAX_DESC are checked
        desc_cache = {}

        def desc(h):
            if h not in desc_cache:
                kind = index.edge_kind(h)
                if kind == "player":
                    desc_cache[h] = 1 + sum(
                        desc(h + (a,)) for a in index.node_at[h][1]
                    )
                elif kind == "chance":
                    desc_cache[h] = sum(
                        desc(h + ("river:" + card_str(c),))
                        for c in index.chance_cards[h]
                    )
                else:
                    desc_cache[h] = 0
            return desc_cache[h]

        for hist in hists:
            kind = index.edge_kind(hist)
            chips = replay_chips(index, hist)
            board = dealt_board(spot.get("board") or (), hist)
            r0, r1 = reach_at(index, hist, ranges[0], ranges[1], blocked)
            ref = edge_value(index, hist, chips, r0, r1, board, pot)
            for own in (0, 1):
                opp = r1 if own == 0 else r0
                mine = r0 if own == 0 else r1
                v = evec._evs_walk(index, hist, chips, board, pot, own,
                                   reach_vec(opp))
                agg = float(np.dot(reach_vec(mine), v))
                d = abs(agg - ref)
                if d > agg_max:
                    agg_max = d
                # parity split: pin both halves of V separately
                half = {c: r for c, r in mine.items()
                        if evec._gid(c) % 2 == 0}
                half0 = half if own == 0 else r0
                half1 = half if own == 1 else r1
                ref_half = edge_value(index, hist, chips, half0, half1,
                                      board, pot)
                agg_half = float(
                    np.dot(reach_vec(half), v))
                d = abs(agg_half - ref_half)
                if d > split_max:
                    split_max = d
            if kind != "player":
                continue
            # persisted vs live evec — every player node, per combo per action
            live = evs_live.get((index.node_at[hist][0], hist), {})
            for combo, row in live.items():
                prow = persisted.get(hist, {}).get(combo, {})
                for a, val in row.items():
                    persist_cells += 1
                    d = abs(val - prow.get(a, 0.0))
                    if d > persisted_max:
                        persisted_max = d
            # exact per-combo check under budget
            if desc(hist) <= MAX_DESC_PER_NODE:
                exact_hists += 1
                ref_evs = ref_action_evs(index, hist, r0, r1, board, pot)
                got = evec.node_action_evs(index, hist, r0, r1, board, pot)
                for combo, row in ref_evs.items():
                    for a, val in row.items():
                        exact_cells += 1
                        for cand in (got.get(combo, {}).get(a),
                                     persisted.get(hist, {}).get(combo, {})
                                     .get(a)):
                            if cand is None:
                                continue
                            d = abs(val - cand)
                            if d > exact_max:
                                exact_max = d
        checks = {
            "aggregate_edge_value": {
                "hists_checked": len(hists), "max_abs_diff": agg_max},
            "parity_split": {"hists_checked": len(hists),
                             "max_abs_diff": split_max},
            "persisted_vs_live_evec": {
                "player_nodes_checked": n_player, "cells": persist_cells,
                "max_abs_diff": persisted_max},
            "exact_per_combo": {
                "player_nodes_checked": exact_hists, "cells": exact_cells,
                "max_abs_diff": exact_max,
                "skipped_hists_over_budget": n_player - exact_hists},
        }
        ok = max(agg_max, split_max, persisted_max, exact_max) <= TOL
        report[name] = {
            "id": jid, "pot": pot, "tol": TOL,
            "hists": len(hists), "player_nodes": n_player,
            "chance_positions": n_chance,
            "checks": checks, "ok": ok,
            "seconds": round(time.time() - t0, 2),
        }
        print("  hists=%d (player=%d chance=%d) agg=%r split=%r "
              "persisted=%r exact=%r (%d cells over %d nodes) %.1fs"
              % (len(hists), n_player, n_chance, agg_max, split_max,
                 persisted_max, exact_max, exact_cells, exact_hists,
                 time.time() - t0), flush=True)
        if not ok:
            failures += 1
    json.dump(report, open(OUT, "w"), indent=1)
    print("\nwrote %s; %d job(s) failed" % (OUT, failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
