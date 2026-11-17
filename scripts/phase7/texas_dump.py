"""Parse a TexasSolver ``dump_result`` JSON into our /strategy node shape.

TexasSolver dump conventions (v0.2.0):
* ``player`` 1 = OOP -> our 0, ``player`` 0 = IP -> our 1
* ``BET x`` -> ``b<x>`` (x = chips added on the action)
* ``RAISE x`` -> ``r<level>`` where ``level`` = raiser's total chips invested
  this spot after the action (x includes the call amount)
* ``CHECK``/``CALL``/``FOLD`` -> ``check``/``call``/``fold``
* chance nodes with ``deal_number > 0`` deal the next street's card -> the card
  enters the history as ``river:<card>``; ``deal_number == 0`` is a terminal.
* strategy dicts give per-combo conditional action frequencies for in-range
  combos only.

Also provides ``dump_ev`` — a pure-Python evaluation of player 0's net EV
(net chips won relative to spot start, pot/2 sunk) by walking the dumped
average strategy, and ``hand_grid`` matching ``api.strategy.hand_grid``.

Python 3.9 safe (runs in the engine venv too).
"""
from __future__ import annotations

import itertools
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from holdem_solver.protocol.cards import parse_range, combos_conflict

RANKS = "AKQJT98765432"
SUITS = "cdhs"

_PLAYER_MAP = {1: 0, 0: 1}
_ACTION_RE = re.compile(r"^(CHECK|CALL|FOLD|BET|RAISE)(?:\s+(-?\d+(?:\.\d+)?))?$")


def _num(x: float) -> str:
    return "%g" % x


def parse_card(text: str) -> int:
    """``"Kh"`` -> card id in our ``4*rank + suit`` convention (SUITS sdch)."""
    rank = RANKS.index(text[0].upper())
    suit = "sdch".index(text[1].lower())
    return 4 * rank + suit


def canonical_combo(text: str) -> str:
    """Normalize a TS combo string (any card order) to our ``combo_str``."""
    a, b = parse_card(text[:2]), parse_card(text[2:])
    lo, hi = min(a, b), max(a, b)
    return "%s%s" % (RANKS[lo // 4] + "sdch"[lo % 4], RANKS[hi // 4] + "sdch"[hi % 4])


def parse_dump(path: str) -> List[Dict[str, Any]]:
    """Walk the dump JSON; return [{player, history, actions, strategy}]."""
    with open(path) as fh:
        root = json.load(fh)
    nodes = []

    def walk(node: Dict[str, Any], history: List[str], chips: Tuple[float, float]) -> None:
        ntype = node.get("node_type")
        if ntype == "chance_node":
            if node.get("deal_number", 0) == 0:
                return  # terminal
            for card, child in node.get("dealcards", {}).items():
                walk(child, history + ["river:" + card], chips)
            return
        if ntype != "action_node":
            raise ValueError("unknown node_type %r" % ntype)

        ours = _PLAYER_MAP[node["player"]]
        strat = node.get("strategy") or {}
        strat_actions = strat.get("actions") or node.get("actions", [])
        strat_combos = strat.get("strategy", {})
        mapped_actions = {}
        for raw in list(node.get("actions", [])) + [a for a in strat_actions if a not in node.get("actions", [])]:
            match = _ACTION_RE.match(raw)
            if match is None:
                raise ValueError("unparseable action %r" % raw)
            kind, amount = match.group(1), match.group(2)
            if kind == "BET":
                name = "b" + _num(float(amount))
            elif kind == "RAISE":
                level = chips[ours] + float(amount)
                name = "r" + _num(level)
            else:
                name = kind.lower()
            mapped_actions[raw] = (name, kind, float(amount) if amount is not None else None)

        strategy = {}
        for combo, freqs in strat_combos.items():
            ours_combo = canonical_combo(combo)
            strategy[ours_combo] = dict(
                (mapped_actions[a][0], float(freqs[i])) for i, a in enumerate(strat_actions)
            )
        nodes.append(
            {
                "player": ours,
                "history": list(history),
                "actions": [mapped_actions[a][0] for a in node["actions"]],
                "strategy": strategy,
            }
        )

        childrens = node.get("childrens") or {}
        for raw in node.get("actions", []):
            name, kind, amount = mapped_actions[raw]
            c0, c1 = chips
            if kind == "CALL":
                level = max(c0, c1)
                chips_next = (level, c1) if ours == 0 else (c0, level)
            elif kind in ("BET", "RAISE"):
                chips_next = (c0 + amount, c1) if ours == 0 else (c0, c1 + amount)
            else:
                chips_next = chips
            child = childrens.get(raw)
            if child is None or (
                child.get("node_type") == "chance_node" and child.get("deal_number", 0) == 0
            ):
                continue  # terminal edge: not a strategy node
            walk(child, history + [name], chips_next)

    walk(root, [], (0.0, 0.0))
    return nodes


# --- hand evaluation -------------------------------------------------------

_RANK_VALUE = {r: 12 - i for i, r in enumerate(RANKS)}  # A=12 ... 2=0


def _rank5(cards: Tuple[int, ...]) -> Tuple[int, Tuple[int, ...]]:
    ranks = sorted((_RANK_VALUE[RANKS[c // 4]] for c in cards), reverse=True)
    flush = len({c % 4 for c in cards}) == 1
    uniq = sorted(set(ranks), reverse=True)
    straight_high = -1
    if len(uniq) == 5 and uniq[0] - uniq[-1] == 4:
        straight_high = uniq[0]
    elif uniq == [12, 3, 2, 1, 0]:  # wheel
        straight_high = 3
    counts = sorted(((ranks.count(r), r) for r in set(ranks)), reverse=True)
    if straight_high >= 0 and flush:
        return (8, (straight_high,))
    if counts[0][0] == 4:
        return (7, (counts[0][1], counts[1][1]))
    if counts[0][0] == 3 and counts[1][0] == 2:
        return (6, (counts[0][1], counts[1][1]))
    if flush:
        return (5, tuple(ranks))
    if straight_high >= 0:
        return (4, (straight_high,))
    if counts[0][0] == 3:
        return (3, tuple(r for _, r in counts))
    if counts[0][0] == 2 and counts[1][0] == 2:
        pairs = sorted((counts[0][1], counts[1][1]), reverse=True)
        return (2, (pairs[0], pairs[1], counts[2][1]))
    if counts[0][0] == 2:
        return (1, tuple(r for _, r in counts))
    return (0, tuple(ranks))


_rank7_cache = {}  # type: Dict[Tuple[int, ...], Tuple[int, Tuple[int, ...]]]


def rank7(cards: Tuple[int, ...]) -> Tuple[int, Tuple[int, ...]]:
    key = tuple(sorted(cards))
    cached = _rank7_cache.get(key)
    if cached is None:
        cached = max(_rank5(sub) for sub in itertools.combinations(key, 5))
        _rank7_cache[key] = cached
    return cached


# --- EV walk ----------------------------------------------------------------


def _combo_tuple(text: str) -> Tuple[int, int]:
    a, b = parse_card(text[:2]), parse_card(text[2:])
    return (a, b)


def dump_ev(
    path: str,
    pot: float,
    board: List[str],
    range0: str,
    range1: str,
) -> float:
    """Player 0's net EV under the dumped average strategy.

    Payoffs: winner collects ``pot + chips0 + chips1``; each player's net is
    ``share * total - own_chips - pot/2``. Reach is the product of the
    player's own action frequencies along the path times the range weight.
    """
    with open(path) as fh:
        root = json.load(fh)
    board_ids = tuple(parse_card(c) for c in board)
    w0 = {c: w for c, w in parse_range(range0.split(",")).items()}
    w1 = {c: w for c, w in parse_range(range1.split(",")).items()}
    # parse_range keys are Combo tuples; rebuild keyed by combo_str
    from holdem_solver.protocol.cards import combo_str

    w0 = {combo_str(c): w for c, w in w0.items()}
    w1 = {combo_str(c): w for c, w in w1.items()}

    def valid_mass(reach0, reach1):
        """Sum of reach products over non-conflicting (actually dealt) pairs."""
        mass = 0.0
        for h0, r0 in reach0.items():
            c0t = _combo_tuple(h0)
            inner = 0.0
            for h1, r1 in reach1.items():
                c1t = _combo_tuple(h1)
                if c0t[0] in c1t or c0t[1] in c1t:
                    continue
                inner += r1
            mass += r0 * inner
        return mass

    def showdown(reach0, reach1, c0, c1, board_now):
        total = pot + c0 + c1
        rank1 = {}
        contrib = 0.0
        for h0, r0 in reach0.items():
            cards0 = _combo_tuple(h0)
            v0 = rank7(board_now + cards0)
            wins = ties = 0.0
            for h1, r1 in reach1.items():
                cards1 = _combo_tuple(h1)
                if cards0[0] in cards1 or cards0[1] in cards1:
                    continue  # pair never dealt
                v1 = rank1.get(h1)
                if v1 is None:
                    v1 = rank7(board_now + cards1)
                    rank1[h1] = v1
                if v0 > v1:
                    wins += r1
                elif v0 == v1:
                    ties += r1
            contrib += r0 * (wins + 0.5 * ties) * total
        return contrib

    def walk(node, chips, reach0, reach1, board_now):
        """Return player-0's summed net payoff over this subtree."""
        ntype = node.get("node_type")
        if ntype == "chance_node":
            if node.get("deal_number", 0) == 0:
                c0, c1 = chips
                return (
                    showdown(reach0, reach1, c0, c1, board_now)
                    - valid_mass(reach0, reach1) * (c0 + pot / 2)
                )
            # TS dumps a subtree for all 52 cards, including cards already on
            # the board; dead-card subtrees are unreachable stubs and must be
            # skipped. Conditional on a dealt pair, the river is uniform over
            # the 52 - len(board) - 4 remaining cards; subtrees conflicting
            # with a pair contribute zero via the filtered reaches below.
            deck_left = 52 - len(board_now) - 4
            possible = [c for c in node.get("dealcards", {})
                        if parse_card(c) not in board_now]
            w = 1.0 / deck_left
            total = 0.0
            for card in possible:
                dealt = parse_card(card)
                # combos containing the dealt card cannot have been dealt
                r0 = {h: r for h, r in reach0.items() if dealt not in _combo_tuple(h)}
                r1 = {h: r for h, r in reach1.items() if dealt not in _combo_tuple(h)}
                total += w * walk(node["dealcards"][card], chips, r0, r1,
                                  board_now + (dealt,))
            return total

        ours = _PLAYER_MAP[node["player"]]
        strat = node.get("strategy") or {}
        strat_actions = strat.get("actions") or node.get("actions", [])
        strat_combos = strat.get("strategy", {})
        reach = reach0 if ours == 0 else reach1
        own = w0 if ours == 0 else w1

        childrens = node.get("childrens") or {}
        total = 0.0
        for raw in node.get("actions", []):
            match = _ACTION_RE.match(raw)
            kind, amount = match.group(1), match.group(2)
            idx = strat_actions.index(raw)
            c0, c1 = chips
            if kind == "CALL":
                level = max(c0, c1)
                chips_next = (level, c1) if ours == 0 else (c0, level)
            elif kind in ("BET", "RAISE"):
                chips_next = (c0 + float(amount), c1) if ours == 0 else (c0, c1 + float(amount))
            else:
                chips_next = chips
            child_reach = dict(reach)
            seen = set()
            for combo, freqs in strat_combos.items():
                ours_combo = canonical_combo(combo)
                seen.add(ours_combo)
                child_reach[ours_combo] = reach.get(
                    ours_combo, own.get(ours_combo, 0.0)
                ) * float(freqs[idx])
            for combo in reach:
                if combo not in seen:
                    child_reach[combo] = 0.0
            next0 = child_reach if ours == 0 else reach0
            next1 = reach1 if ours == 0 else child_reach
            child = childrens.get(raw)
            if kind == "FOLD":
                mass = valid_mass(next0, next1)
                total += mass * ((pot / 2 + c1) if ours == 1 else (-pot / 2 - c0))
                continue
            if child is None or (
                child.get("node_type") == "chance_node" and child.get("deal_number", 0) == 0
            ):
                # street ended (call/check-check): showdown payoff on the river
                total += showdown(next0, next1, chips_next[0], chips_next[1], board_now)
                total -= valid_mass(next0, next1) * (chips_next[0] + pot / 2)
                continue
            total += walk(child, chips_next, next0, next1, board_now)
        return total

    blocked = set(board_ids)
    init0 = {c: w for c, w in w0.items() if not (set(_combo_tuple(c)) & blocked)}
    init1 = {c: w for c, w in w1.items() if not (set(_combo_tuple(c)) & blocked)}
    norm = valid_mass(init0, init1)
    if norm <= 0:
        raise ValueError("no dealt pairs: ranges fully blocked by board")
    return walk(root, (0.0, 0.0), init0, init1, board_ids) / norm
