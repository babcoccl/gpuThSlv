"""Phase 10.3: independent brute-force EV check on the persisted turn spot.

Shares no solver-side evaluation code: showdowns are evaluated from scratch
with the ``brute_ev`` 7-card scorer; the tree is walked pairwise — one
opponent combo ``d`` at a time with scalar reach products, enumerating every
river card at chance positions (uniform ``1/deck_left``, combos holding the
dealt card skipped). Compared against the persisted per-combo per-action EVs
exactly as ``/node`` serves them: ``EV(c) = Σ_a σ(c,a)·evs[hist][c][a]``.

Checked histories on the scale-turn-chance job: the root, one mid-turn node,
and one node behind each of two different ``river:`` chance edges.

Runs in ``.venv-api``. Writes ``runs/brute_turn_ev.json``; exits 1 if any
hand EV differs from the served values by more than TOL.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.cards import card_id, card_str, parse_cards  # noqa: E402
from holdem_solver.protocol.evwalk import (  # noqa: E402
    apply_action,
    combo_tuple,
    dealt_board,
    replay_chips,
)
from holdem_solver.api.nodes import load_index, load_node_evs  # noqa: E402
from holdem_solver.api.strategy import spot_ranges  # noqa: E402
from scripts.phase9.brute_ev import conflict, score7  # noqa: E402

HERE = os.path.dirname(__file__)
DATA_DIR = os.path.expanduser("~/.local/share/holdem-solver")
JOB = "9eb2cec01565"  # scale-turn-chance
OUT = os.path.join(HERE, "runs", "brute_turn_ev.json")

TOL = 1e-9


def _rs(card_id):
    """protocol card id -> brute_ev (rank, suit) pair."""
    return (card_id // 4, card_id % 4)


def _id(rs):
    return 4 * rs[0] + rs[1]


def _showdown_payoff(board, c, d, chips, pot):
    """p0's net payoff at showdown when p0=c, p1=d (c/d are (r,s) tuples)."""
    v0 = score7(tuple(board) + c)
    v1 = score7(tuple(board) + d)
    share = 1.0 if v0 > v1 else 0.5 if v0 == v1 else 0.0
    total = pot + chips[0] + chips[1]
    return share * total - chips[0] - pot / 2.0


def _pair_walk(index, hist, chips, combos, probs, board, pot):
    """p0's summed payoff × joint reach with exactly one combo per player."""
    kind = index.edge_kind(hist)
    if kind == "terminal":
        if conflict(combos[0], combos[1]):
            return 0.0
        return probs[0] * probs[1] * _showdown_payoff(
            board, combos[0], combos[1], chips, pot)
    if kind == "chance":
        deck_left = 52 - len(board) - 4
        total = 0.0
        for card in index.chance_cards[hist]:
            if _rs(card) in board:
                continue
            if _rs(card) in combos[0] or _rs(card) in combos[1]:
                continue  # this pair could never hold the dealt card
            total += (1.0 / deck_left) * _pair_walk(
                index, hist + ("river:" + card_str(card),), chips, combos,
                probs, board + (_rs(card),), pot)
        return total
    player, actions = index.node_at[hist]
    rows = index.rows_at[hist]
    combo = combos[player]  # (rank, suit) pair
    i0, i1 = _id(combo[0]), _id(combo[1])
    lo, hi = (i0, i1) if i0 < i1 else (i1, i0)
    key = card_str(lo) + card_str(hi)
    freqs = rows.get(key) or {}
    total = 0.0
    for a in actions:
        sigma = float(freqs.get(a, 0.0))
        if sigma <= 0.0:
            continue
        p_next = dict(probs)
        p_next[player] = probs[player] * sigma
        nc = apply_action(chips, player, a)
        if a == "fold":
            if conflict(combos[0], combos[1]):
                continue
            v = (pot / 2.0 + nc[1]) if player == 1 else (-pot / 2.0 - nc[0])
            total += p_next[0] * p_next[1] * v
            continue
        total += _pair_walk(index, hist + (a,), nc, combos, p_next,
                            board, pot)
    return total


def brute_hand_ev(index, hist, combo_key, reach_opp, board, pot, pc_player):
    """Conditional hand EV of ``combo_key`` at ``hist`` from scratch."""
    c = tuple(_rs(i) for i in combo_tuple(combo_key))
    board_now = tuple(_rs(i) for i in dealt_board(board, hist))
    chips = replay_chips(index, hist)
    num = den = 0.0
    for d_key, r_d in reach_opp.items():
        if r_d <= 0.0:
            continue
        d = tuple(_rs(i) for i in combo_tuple(d_key))
        if conflict(c, d) or set(d) & set(board_now):
            continue
        combos = {pc_player: c, 1 - pc_player: d}
        v = _pair_walk(index, hist, chips, combos, {0: 1.0, 1: 1.0},
                       board_now, pot)
        num += r_d * v
        den += r_d
    if den <= 0.0:
        return None
    ev0 = num / den
    return ev0 if pc_player == 0 else -ev0


def served_hand_ev(index, hist, evs_p, combo_key):
    """``EV(c) = Σ_a σ(c,a)·evs[hist][c][a]`` — the /node aggregation."""
    freqs = index.rows_at[hist].get(combo_key) or {}
    row = evs_p.get(hist, {}).get(combo_key, {})
    return sum(float(freqs.get(a, 0.0)) * float(v) for a, v in row.items())


def main():
    strat = os.path.join(DATA_DIR, "strategy-%s.npz" % JOB)
    evs_path = os.path.join(DATA_DIR, "strategy-%s.evs.npz" % JOB)
    index, _ = load_index(strat)
    keys = index.keys_list  # type: ignore[attr-defined]
    db = sqlite3.connect(os.path.join(DATA_DIR, "solves.db"))
    spot = json.loads(db.execute(
        "select spot from jobs where id = ?", (JOB,)).fetchone()[0])
    ranges = spot_ranges(spot)
    pot = float(spot.get("pot", 20.0))
    board = spot.get("board") or ()

    persisted, _masses = load_node_evs(evs_path, keys)

    from holdem_solver.protocol.evwalk import initial_reaches
    blocked = parse_cards(board) + parse_cards(spot.get("dead_cards") or ())
    init0, init1 = initial_reaches(ranges[0], ranges[1], blocked)

    def reach(index_, hist, r0, r1):
        from holdem_solver.protocol.evwalk import reach_at
        return reach_at(index_, hist, ranges[0], ranges[1], blocked)

    # pick histories: root, one mid-turn node, one hist behind each of two
    # different chance edges, plus one deep river node
    chance_pos = sorted(index.chance_cards)
    hists = [()]
    mid = sorted(h for h in index.node_at if len(h) == 2)
    if mid:
        hists.append(mid[0])
    for pos in chance_pos[:2]:
        card = sorted(index.chance_cards[pos])[0]
        dealt = pos + ("river:" + card_str(card),)
        hists.append(dealt)
        deep = sorted(h for h in index.node_at
                      if h[: len(dealt)] == dealt and len(h) > len(dealt))
        if deep:
            hists.append(deep[0])

    results = {}
    worst = 0.0
    for hist in hists:
        if index.edge_kind(hist) != "player":
            continue
        player, actions = index.node_at[hist]
        r0, r1 = reach(index, hist, init0, init1)
        reach_opp = r1 if player == 0 else r0
        reach_own = r0 if player == 0 else r1
        rows = index.rows_at[hist]
        compared = 0
        max_d = 0.0
        worst_combo = None
        for combo_key in sorted(rows):
            if reach_own.get(combo_key, 0.0) <= 0.0:
                continue
            brute = brute_hand_ev(index, hist, combo_key, reach_opp,
                                  board, pot, player)
            if brute is None:
                continue
            served = served_hand_ev(index, hist, persisted, combo_key)
            d = abs(brute - served)
            compared += 1
            if d > max_d:
                max_d = d
                worst_combo = combo_key
        label = ",".join(hist) or "root"
        results[label] = {"player": player, "compared": compared,
                          "max_abs_diff": max_d, "worst_combo": worst_combo,
                          "ok": max_d <= TOL}
        worst = max(worst, max_d)
        print("  %s: %d combos, max |d|=%r worst=%s"
              % (label, compared, max_d, worst_combo), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"job": JOB, "tol": TOL, "nodes": results,
               "ok": all(r["ok"] for r in results.values())},
              open(OUT, "w"), indent=1)
    n_ok = sum(1 for r in results.values() if r["ok"])
    print("\n%d/%d histories within %.0e; worst |d|=%r"
          % (n_ok, len(results), TOL, worst))
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
