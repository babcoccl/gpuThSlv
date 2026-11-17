"""Phase 9.1: independent brute-force hand-EV check.

Deliberately shares NO code with ``scripts/phase7/texas_dump.py::dump_ev``
and ``holdem_solver/protocol/evwalk.py``: own card parsing, own 7-card
showdown evaluator, own TexasSolver dump-JSON walk.

For two river spots it computes each acting combo's **hand EV** at the root
and at one child node by brute force: every compatible opponent combo is
enumerated and the subtree is walked once per (own, opp) pair with scalar
reach products — no reach dictionaries, no mass aggregation.

``EV(c) = Σ_d reach_d · v(c, d) / Σ_d reach_d`` where ``v(c, d)`` is the
expected payoff to ``c``'s player when exactly ``c`` and ``d`` were dealt,
computed by a pairwise tree walk from the target node.

It then compares against:

* the ``evwalk.node_action_evs`` per-action conditional EVs aggregated by
  the profile (``hand EV = Σ_a σ(c,a)·EV(c,a)``);
* the Phase 8 ``xeval.node_action_evs`` reference the same way;
* the brute-force root EV (Σ_c w_c·EV(c)/Σ w_c) against ``root_ev`` /
  ``dump_ev``.

Runs in ``.venv-engine``. Usage:
.venv-engine/bin/python -m scripts.phase9.brute_ev
Writes scripts/phase9/runs/brute_ev.json.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

RUNS = os.path.join(os.path.dirname(__file__), "runs")
OUT = os.path.join(RUNS, "brute_ev.json")
TS_DUMPS = os.path.join(os.path.dirname(__file__), "..", "phase8", "runs")

SPOT_NAMES = ("w-toy-check", "w-dry-cap0")  # the two river spots checked

TOL_FRAC_OF_POT = 0.01  # standing tolerance: 1.0% of pot
TOL_CHIPS = 0.05

# --- own card machinery ------------------------------------------------------
# rank char -> 0..12 (A high), suit char -> 0..3 in "cdhs" order; a card is a
# (rank, suit) pair of small ints. Rendered for comparison as "<R><s>" with
# ranks "AKQJT98765432" and suits "sdch" — the shared wire format only.

_RANK_CH = "AKQJT98765432"
_SUIT_CH = "cdhs"


def card(text):
    """``"Ks"`` -> (rank index, suit index)."""
    return (_RANK_CH.index(text[0].upper()), _SUIT_CH.index(text[1].lower()))


def card_text(c):
    return _RANK_CH[c[0]] + _SUIT_CH[c[1]]


def combo_text(c0, c1):
    """Canonical combo string matching the dump's ``canonical_combo``
    convention: the card with the smaller (rank*4+suit) flat index first."""
    f0, f1 = 4 * c0[0] + c0[1], 4 * c1[0] + c1[1]
    lo, hi = (c0, c1) if f0 < f1 else (c1, c0)
    return card_text(lo) + card_text(hi)


def parse_combo(text):
    return combo_text(card(text[:2]), card(text[2:])), (card(text[:2]), card(text[2:]))


def conflict(ca, cb):
    return ca[0] == cb[0] or ca[0] == cb[1] or ca[1] == cb[0] or ca[1] == cb[1]


# --- own 7-card showdown evaluator -------------------------------------------
# Score: (category, tiebreak ranks desc). Category: straight flush 8,
# quads 7, full house 6, flush 5, straight 4, trips 3, two pair 2, pair 1,
# high card 0. Higher tuple wins.

def _score5(cards):
    ranks = [c[0] for c in cards]  # 0 = A ... 12 = 2
    suits = [c[1] for c in cards]
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    flush = len(set(suits)) == 1
    uniq = sorted(set(ranks))
    straight_high = None
    if len(uniq) == 5 and uniq[-1] - uniq[0] == 4:
        straight_high = uniq[0]
    elif uniq == [0, 9, 10, 11, 12]:  # A-2-3-4-5 wheel
        straight_high = 9
    if flush and straight_high is not None:
        return (8, (-straight_high,))
    if groups[0][1] == 4:
        return (7, (-groups[0][0], -groups[1][0]))
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, (-groups[0][0], -groups[1][0]))
    if flush:
        return (5, tuple(-r for r in sorted(ranks)))
    if straight_high is not None:
        return (4, (-straight_high,))
    if groups[0][1] == 3:
        kickers = tuple(-g[0] for g in groups[1:])
        return (3, (-groups[0][0],) + kickers)
    if groups[0][1] == 2 and groups[1][1] == 2:
        pair_r = sorted((groups[0][0], groups[1][0]))
        return (2, (-pair_r[0], -pair_r[1], -groups[2][0]))
    if groups[0][1] == 2:
        kickers = tuple(-g[0] for g in groups[1:])
        return (1, (-groups[0][0],) + kickers)
    return (0, tuple(-r for r in sorted(ranks)))


def score7(cards):
    """Best 5-card score over the 21 subsets of a 7-card hand."""
    return max(_score5(sub) for sub in itertools.combinations(cards, 5))


# --- own range parsing --------------------------------------------------------
# Supports the token forms used by the checked spots: pairs (``AA``), rank
# classes (``AKs`` / ``AKo`` / ``AK``), pair/kicker ranges (``TT+``,
# ``A2s-A5s``, ``77-TT``), explicit combos (``AhKh``), ``*``, and ``:weight``.


def _class(high_t, low_t, kind):
    """Combos (as (card, card) tuples) of rank class high/low + kind."""
    hi, lo = _RANK_CH.index(high_t.upper()), _RANK_CH.index(low_t.upper())
    if hi > lo:
        hi, lo = lo, hi
    out = []
    if hi == lo:
        for a in range(4):
            for b in range(a + 1, 4):
                out.append(((hi, a), (hi, b)))
        return out
    for sa in range(4):
        for sb in range(4):
            if kind == "s" and sa != sb:
                continue
            if kind == "o" and sa == sb:
                continue
            out.append(((hi, sa), (lo, sb)))
    return out


def _token_combos(token):
    if token in ("*", "random", "any"):
        return [((ra, sa), (rb, sb))
                for ra in range(13) for sa in range(4)
                for rb in range(13) for sb in range(4)
                if (ra, sa) != (rb, sb)]
    if len(token) == 4 and token[1].lower() in _SUIT_CH and token[3].lower() in _SUIT_CH:
        return [(card(token[:2]), card(token[2:]))]
    body, kind = token, ""
    if len(body) == 3:
        body, kind = body[:2], body[2].lower()
    if len(body) == 2 and body[0].upper() in _RANK_CH and body[1].upper() in _RANK_CH:
        return _class(body[0], body[1], kind)
    if body.endswith("+"):
        core = body[:-1]
        k = ""
        if len(core) == 3:
            core, k = core[:2], core[2].lower()
        hi, lo = _RANK_CH.index(core[0].upper()), _RANK_CH.index(core[1].upper())
        if hi > lo:
            hi, lo = lo, hi
        if hi == lo:
            out = []
            for r in range(0, hi + 1):
                out.extend(_class(_RANK_CH[r], _RANK_CH[r], ""))
            return out
        out = []
        for kicker in range(hi + 1, lo + 1):
            out.extend(_class(_RANK_CH[hi], _RANK_CH[kicker], k))
        return out
    if "-" in token:
        left, _, right = token.partition("-")
        lk = left[2].lower() if len(left) == 3 else ""
        lh, ll = left[:2]
        rh, rl = right[:2]
        if lh == ll and rh == rl:
            a = _RANK_CH.index(lh.upper())
            b = _RANK_CH.index(rh.upper())
            lo_r, hi_r = min(a, b), max(a, b)
            out = []
            for r in range(lo_r, hi_r + 1):
                out.extend(_class(_RANK_CH[r], _RANK_CH[r], ""))
            return out
        a = _RANK_CH.index(ll.upper())
        b = _RANK_CH.index(rl.upper())
        lo_r, hi_r = min(a, b), max(a, b)
        out = []
        for kicker in range(lo_r, hi_r + 1):
            out.extend(_class(lh, _RANK_CH[kicker], lk))
        return out
    raise ValueError("cannot parse range token %r" % (token,))


def parse_range(text):
    """``{combo_key: weight}`` keyed by canonical combo text."""
    weights = {}
    for token in text.replace(",", " ").split():
        body, sep, wtext = token.partition(":")
        weight = float(wtext) if sep else 1.0
        for c in _token_combos(body):
            key = combo_text(c[0], c[1])
            weights[key] = weight
    return weights


# --- own dump-JSON walk --------------------------------------------------------

_ACTION = re.compile(r"^(CHECK|CALL|FOLD|BET|RAISE)(?:\s+(-?\d+(?:\.\d+)?))?$")
_PLAYER_MAP = {1: 0, 0: 1}  # TS player 1 (OOP) -> our 0


def canon_action(raw, ours, chips):
    """Raw dump action -> our canonical name (check/call/fold/b<amt>/r<level>)."""
    m = _ACTION.match(raw)
    kind, amount = m.group(1), m.group(2)
    if kind == "BET":
        return "b%g" % float(amount)
    if kind == "RAISE":
        return "r%g" % (chips[ours] + float(amount))
    return kind.lower()


def next_chips(chips, player, kind, amount):
    c0, c1 = chips
    if kind == "CALL":
        level = max(c0, c1)
        return (level, c1) if player == 0 else (c0, level)
    if kind in ("BET", "RAISE"):
        return (c0 + float(amount), c1) if player == 0 else (c0, c1 + float(amount))
    return chips


class DumpTree:
    """The dump JSON pre-parsed into plain (node) dicts; ``children`` maps
    canonical action name -> child node (None = terminal edge)."""

    def __init__(self, path):
        with open(path) as fh:
            self.root = json.load(fh)

    def nodes(self):
        """Yield (player, history, node, chips) for every action node."""
        out = []

        def rec(node, history, chips):
            if node.get("node_type") == "chance_node":
                if node.get("deal_number", 0) == 0:
                    return
                for c, child in node.get("dealcards", {}).items():
                    rec(child, history + ("river:" + c,), chips)
                return
            ours = _PLAYER_MAP[node["player"]]
            out.append((ours, tuple(history), node, chips))
            for raw in node.get("actions", []):
                kind, amount = _ACTION.match(raw).groups()
                nc = next_chips(chips, ours, kind, amount)
                child = (node.get("childrens") or {}).get(raw)
                if child is None or (
                    child.get("node_type") == "chance_node"
                    and child.get("deal_number", 0) == 0
                ):
                    continue
                rec(child, history + (canon_action(raw, ours, chips),), nc)

        rec(self.root, (), (0.0, 0.0))
        return out


def _fix_raise_names(node, chips):
    """Strategy frequencies keyed by canonical name with raise levels
    resolved at ``chips`` (``r<level>`` depends on the raiser's chips)."""
    ours = _PLAYER_MAP[node["player"]]
    strat = node.get("strategy") or {}
    strat_actions = strat.get("actions") or node.get("actions", [])
    rows = {}
    for raw_combo, freqs in strat.get("strategy", {}).items():
        key = parse_combo(raw_combo)[0]
        rows[key] = {}
        for i, raw in enumerate(strat_actions):
            rows[key][canon_action(raw, ours, chips)] = float(freqs[i])
    names = [canon_action(raw, ours, chips) for raw in node.get("actions", [])]
    return rows, names


def combo_tuple(text):
    """canonical combo text -> ((r, s), (r, s))."""
    return card(text[:2]), card(text[2:])


def board_of(hist, board_ids):
    """Board cards at ``hist``: spot board plus dealt ``river:`` cards."""
    out = list(board_ids)
    for elem in hist:
        if elem.startswith("river:"):
            out.append(card(elem[6:]))
    return tuple(out)


class HistMap:
    """hist -> (player, node, chips) for every action node in a DumpTree."""

    def __init__(self, tree):
        self.by_hist = {}
        for player, hist, node, chips in tree.nodes():
            self.by_hist[hist] = (player, node, chips)

    def get(self, hist):
        return self.by_hist.get(tuple(hist))


def pairwise_ev(node, chips, pc_combo, pd_combo, board_now,
                pot, pc_player):
    """Expected payoff to player 0 when pc holds ``pc_combo`` and pd holds
    ``pd_combo``, walking ``node``'s subtree with scalar reach products.

    ``pc`` is the target combo; ``pd`` the enumerated opponent combo.
    Returns p0's expected payoff times the joint path probability.
    """
    combos = {pc_player: pc_combo, 1 - pc_player: pd_combo}
    probs = {pc_player: 1.0, 1 - pc_player: 1.0}
    return _walk_pair(node, chips, combos, probs, board_now, pot)


def _walk_pair(node, chips, combos, probs, board_now, pot):
    """p0's payoff × joint reach, with exactly one combo per player."""
    if node.get("node_type") == "chance_node":
        if node.get("deal_number", 0) == 0:
            c0, c1 = chips
            if conflict(combos[0], combos[1]):
                return 0.0
            share0 = _share(board_now, combos[0], combos[1])
            return probs[0] * probs[1] * (
                share0 * (pot + c0 + c1) - c0 - pot / 2.0
            )
        deck_left = 52 - len(board_now) - 4
        total = 0.0
        dealt_flat = {4 * r + s for r, s in board_now}
        for c, child in node.get("dealcards", {}).items():
            r, s = card(c)
            if 4 * r + s in dealt_flat:
                continue
            if (r, s) in combos[0] or (r, s) in combos[1]:
                continue  # this pair could never have been dealt here
            total += (1.0 / deck_left) * _walk_pair(
                child, chips, combos, probs, board_now + ((r, s),), pot
            )
        return total

    ours = _PLAYER_MAP[node["player"]]
    rows, names = _fix_raise_names(node, chips)
    combo = combos[ours]
    key = combo_text(combo[0], combo[1])
    freqs = rows.get(key) or {}
    childrens = node.get("childrens") or {}
    total = 0.0
    for i, raw in enumerate(node.get("actions", [])):
        kind, amount = _ACTION.match(raw).groups()
        name = names[i]
        sigma = float(freqs.get(name, 0.0))
        if sigma <= 0.0:
            continue
        nc = next_chips(chips, ours, kind, amount)
        p_next = dict(probs)
        p_next[ours] = probs[ours] * sigma
        child = childrens.get(raw)
        if kind == "FOLD":
            if conflict(combos[0], combos[1]):
                continue
            v = (pot / 2.0 + nc[1]) if ours == 1 else (-pot / 2.0 - nc[0])
            total += p_next[0] * p_next[1] * v
            continue
        if child is None or (
            child.get("node_type") == "chance_node"
            and child.get("deal_number", 0) == 0
        ):
            if conflict(combos[0], combos[1]):
                continue
            share0 = _share(board_now, combos[0], combos[1])
            total += p_next[0] * p_next[1] * (
                share0 * (pot + nc[0] + nc[1]) - nc[0] - pot / 2.0
            )
            continue
        total += _walk_pair(child, nc, combos, p_next, board_now, pot)
    return total


def _share(board_now, c0, c1):
    v0 = score7(tuple(board_now) + c0)
    v1 = score7(tuple(board_now) + c1)
    return 1.0 if v0 > v1 else 0.5 if v0 == v1 else 0.0


def combo_tuple(text):
    """canonical combo text -> ((r, s), (r, s))."""
    return card(text[:2]), card(text[2:])


def board_of(hist, board_ids):
    """Board cards at ``hist``: spot board plus dealt ``river:`` cards."""
    out = list(board_ids)
    for elem in hist:
        if elem.startswith("river:"):
            out.append(card(elem[6:]))
    return tuple(out)


class HistMap:
    """hist -> (player, node, chips) for every action node in a DumpTree."""

    def __init__(self, tree):
        self.by_hist = {}
        for player, hist, node, chips in tree.nodes():
            self.by_hist[hist] = (player, node, chips)

    def get(self, hist):
        return self.by_hist.get(tuple(hist))


def reaches_at(hmap, target_hist, w0, w1, board_ids):
    """Per-combo reach at ``target_hist`` for both players: range weight ×
    own action frequencies along the history (independent recompute)."""
    reach = {0: dict(w0), 1: dict(w1)}
    dealt = set(board_ids)
    hist = ()
    for elem in target_hist:
        if elem.startswith("river:"):
            dealt.add(card(elem[6:]))
            for p in (0, 1):
                for key in reach[p]:
                    if card(key[:2]) == card(elem[6:]) or \
                            card(key[2:]) == card(elem[6:]):
                        reach[p][key] = 0.0
            hist = hist + (elem,)
            continue
        entry = hmap.get(hist)
        if entry is None:
            raise ValueError("no action node at %r" % (hist,))
        ours, node, chips = entry
        rows, _names = _fix_raise_names(node, chips)
        for key in reach[ours]:
            sigma = rows.get(key, {}).get(elem, 0.0)
            reach[ours][key] *= sigma
        hist = hist + (elem,)
    # board-blocked combos never reach
    for p in (0, 1):
        for key in reach[p]:
            if set(combo_tuple(key)) & dealt:
                reach[p][key] = 0.0
    return reach


def brute_hand_ev(hmap, hist, combo_key, w0, w1, board_ids, pot):
    """Brute-force hand EV of ``combo_key`` at ``hist``.

    reach_d = opp's per-combo reach at ``hist`` (own code path). For each
    compatible opponent combo the subtree is walked once with singleton
    reaches; ``EV(c) = Σ_d reach_d·v(c,d) / Σ_d reach_d``, in the acting
    player's chips.
    """
    entry = hmap.get(hist)
    if entry is None:
        raise ValueError("no action node at %r" % (hist,))
    ours, node, chips = entry
    reach = reaches_at(hmap, hist, w0, w1, board_ids)
    c = combo_tuple(combo_key)
    if reach[ours].get(combo_key, 0.0) <= 0.0:
        return None
    opp = 1 - ours
    board_set = set(board_ids)
    board_now = board_of(hist, board_ids)
    num = den = 0.0
    for d_key, r_d in reach[opp].items():
        if r_d <= 0.0:
            continue
        d = combo_tuple(d_key)
        if conflict(c, d) or set(d) & board_set:
            continue
        v = pairwise_ev(node, chips, c, d, board_now, pot, ours)
        num += r_d * v
        den += r_d
    if den <= 0.0:
        return None
    ev0 = num / den
    return ev0 if ours == 0 else -ev0


def brute_root_ev(tree, w0, w1, board_ids, pot):
    """Σ_c,d w_c·w_d·v(c,d) / Σ w_c·w_d over compatible pairs — p0's net EV."""
    root = tree.root
    board_set = set(board_ids)
    num = den = 0.0
    for c_key, w_c in w0.items():
        if w_c <= 0.0:
            continue
        c = combo_tuple(c_key)
        if set(c) & board_set:
            continue
        for d_key, w_d in w1.items():
            if w_d <= 0.0:
                continue
            d = combo_tuple(d_key)
            if conflict(c, d) or set(d) & board_set:
                continue
            v = pairwise_ev(root, (0.0, 0.0), c, d,
                            tuple(board_ids), pot, 0)
            num += w_c * w_d * v
            den += w_c * w_d
    return num / den


def main():
    from holdem_solver.protocol.cards import combo_str, parse_cards, parse_range as ref_parse
    from holdem_solver.protocol.evwalk import (
        TreeIndex, dealt_board, node_action_evs, reach_at, root_ev,
    )
    from scripts.phase7.texas_dump import parse_dump
    from scripts.phase8.spots import SPOTS

    os.makedirs(RUNS, exist_ok=True)
    spots = [s for s in SPOTS if s.name in SPOT_NAMES]
    results = {}
    worst_overall = 0.0
    for spot in spots:
        name = spot.name
        print("=== %s ===" % name, flush=True)
        dump_path = os.path.join(TS_DUMPS, "%s_ts.json" % name)
        pot = spot.pot
        limit = TOL_FRAC_OF_POT * pot + TOL_CHIPS
        board_ids = tuple(parse_cards(spot.board))
        dead_ids = tuple(parse_cards(spot.dead_cards or ()))
        blocked = board_ids + dead_ids
        w0 = {combo_str(c): w for c, w in ref_parse(spot.range0).items()}
        w1 = {combo_str(c): w for c, w in ref_parse(spot.range1).items()}
        my_w0 = parse_range(spot.range0)
        my_w1 = parse_range(spot.range1)
        my_board = [card(c) for c in spot.board]

        ts_nodes = parse_dump(dump_path)
        by_node = {}
        for node in ts_nodes:
            hist = tuple(node["history"])
            by_node.setdefault((node["player"], hist), {}).update(node["strategy"])
        index = TreeIndex(by_node)
        tree = DumpTree(dump_path)
        hmap = HistMap(tree)

        spot_out = {"pot": pot, "tol_chips": limit, "nodes": {}}
        # root node (player 0) + one child node: the check child (player 1)
        targets = [(0, ()), (1, ("check",))]

        for player, hist in targets:
            label = "root" if hist == () else ",".join(hist)
            reach0, reach1 = reach_at(index, hist, w0, w1, blocked)
            board_now = dealt_board(spot.board, hist)
            ref = node_action_evs(index, hist, reach0, reach1, board_now, pot)
            node_rows = index.rows_at[hist]
            compared = 0
            max_diff = 0.0
            worst = None
            for combo_key in node_rows:
                # profile-aggregated hand EV from the evwalk per-action EVs
                freqs = node_rows[combo_key]
                ev_parts = ref.get(combo_key)
                if ev_parts is None:
                    continue
                hand_ev = sum(
                    float(freqs.get(a, 0.0)) * float(ev_parts[a])
                    for a in ev_parts
                )
                brute = brute_hand_ev(
                    hmap, hist, combo_key, my_w0, my_w1, my_board, pot
                )
                if brute is None:
                    continue
                d = abs(brute - hand_ev)
                compared += 1
                if d > max_diff:
                    max_diff = d
                    worst = combo_key
            spot_out["nodes"][label] = {
                "player": player,
                "compared": compared,
                "max_abs_diff": max_diff,
                "worst_combo": worst,
                "ok": max_diff <= limit and compared > 0,
            }
            worst_overall = max(worst_overall, max_diff)
            print("  %s: %d combos compared, max |d|=%r (tol %.3f) worst=%s"
                  % (label, compared, max_diff, limit, worst), flush=True)

        # brute root EV vs evwalk.root_ev
        my_root = brute_root_ev(tree, my_w0, my_w1, my_board, pot)
        ref_root = root_ev(index, w0, w1, board_ids, dead_ids, pot)
        root_diff = abs(my_root - ref_root)
        worst_overall = max(worst_overall, root_diff)
        spot_out["root_ev"] = {
            "brute": my_root, "evwalk": ref_root,
            "abs_diff": root_diff, "ok": root_diff <= limit,
        }
        print("  root ev: brute=%r evwalk=%r |d|=%r"
              % (my_root, ref_root, root_diff), flush=True)
        spot_out["ok"] = all(
            n["ok"] for n in spot_out["nodes"].values()
        ) and spot_out["root_ev"]["ok"]
        results[name] = spot_out
        json.dump(results, open(OUT, "w"), indent=1)

    n_ok = sum(1 for r in results.values() if r["ok"])
    print("\n%d/%d spots within tolerance; worst |d|=%r" % (n_ok, len(results), worst_overall))
    print("written to %s" % OUT)
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
