"""Pure-Python EV / equity evaluation over an exported average strategy.

Phase 9. The exported strategy (``p<player>|<hand>|<history>`` keys with
per-action frequencies) already encodes the whole betting tree: player nodes
live at the histories present in the keys, a turn street-end appears as a
``river:<card>`` element, and edges with no continuation are terminals.
This module rebuilds that structure, replays the canonical action grammar
(``check`` / ``fold`` / ``call`` / ``b<amt>`` / ``r<amt>``), and walks
subtrees with reach dictionaries — the same conventions as
``scripts/phase7/texas_dump.py::dump_ev`` and
``scripts/phase8/xeval.py::node_action_evs`` so results can be compared
node for node.

Conventions:

* chips ``(c0, c1)`` are each player's total contribution this spot;
* ``walk`` / ``edge_value`` return player 0's *summed* net payoff over the
  subtree (net chips won, ``pot/2`` sunk), unnormalized — normalize by
  ``valid_mass`` of the entering reaches;
* per-combo action EVs are the acting player's conditional EV: the child
  subtree value with ``reach_p = {combo: 1}`` divided by the opponent reach
  mass over non-conflicting combos, sign-flipped for player 1;
* history elements: action names, plus ``river:<card>`` for dealt cards.

Pure standard library, Python 3.9 syntax (shared with the engine venv).
"""
from __future__ import annotations

import itertools
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cards import RANKS, card_id, card_str, parse_cards

Combo = Tuple[int, int]

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


def combo_tuple(text: str) -> Combo:
    """``"AhQh"`` -> (card id, card id)."""
    return (card_id(text[:2]), card_id(text[2:]))


def combos_conflict(a: Combo, b: Combo) -> bool:
    return a[0] in b or a[1] in b


_ACTION_RE = re.compile(r"^(check|fold|call|b(\d+(?:\.\d+)?)|r(\d+(?:\.\d+)?))$")


def apply_action(chips: Tuple[float, float], player: int, action: str) -> Tuple[float, float]:
    """Replay one canonical action on the contribution pair."""
    c0, c1 = chips
    match = _ACTION_RE.match(action)
    if match is None:
        raise ValueError("unparseable action %r" % (action,))
    if action == "call":
        level = max(c0, c1)
        return (level, c1) if player == 0 else (c0, level)
    if match.group(2) is not None:  # b<amt>: add chips
        amount = float(match.group(2))
        return (c0 + amount, c1) if player == 0 else (c0, c1 + amount)
    if match.group(3) is not None:  # r<amt>: raise to total contribution
        level = float(match.group(3))
        return (level, c1) if player == 0 else (c0, level)
    return chips  # check / fold


class TreeIndex:
    """Node structure derived from ``{(player, hist): {combo: {action: freq}}}``.

    ``hist`` is a tuple of history elements (action names and ``river:<card>``
    markers). A history with player rows is a decision node; a history with
    no rows but ``river:`` children is a chance (street-end) position.
    """

    def __init__(self, by_node: Dict[Tuple[int, Tuple[str, ...]], Dict[str, Dict[str, float]]]) -> None:
        self.by_node = by_node
        self.node_at = {}  # type: Dict[Tuple[str, ...], Tuple[int, List[str]]]
        self.rows_at = {}  # type: Dict[Tuple[str, ...], Dict[str, Dict[str, float]]]
        self.chance_cards = {}  # type: Dict[Tuple[str, ...], List[int]]
        for (player, hist), rows in by_node.items():
            self.node_at[hist] = (player, list(next(iter(rows.values()))))
            self.rows_at[hist] = rows
            for i, elem in enumerate(hist):
                if elem.startswith("river:"):
                    prefix = hist[:i]
                    self.chance_cards.setdefault(prefix, [])
                    card = card_id(elem[6:])
                    if card not in self.chance_cards[prefix]:
                        self.chance_cards[prefix].append(card)
        for cards in self.chance_cards.values():
            cards.sort()

    def edge_kind(self, hist: Tuple[str, ...]) -> str:
        """``player`` / ``chance`` / ``terminal`` for the position at ``hist``."""
        if hist in self.node_at:
            return "player"
        if hist in self.chance_cards:
            return "chance"
        return "terminal"

    def descendants(self, hist: Tuple[str, ...]) -> int:
        """Number of player nodes under ``hist`` (0 for a terminal edge)."""
        count = 0
        stack = [hist]
        seen = set()
        while stack:
            cur = stack.pop()
            kind = self.edge_kind(cur)
            if kind == "player":
                if cur in seen:
                    continue
                seen.add(cur)
                count += 1
                for action in self.node_at[cur][1]:
                    stack.append(cur + (action,))
            elif kind == "chance":
                for card in self.chance_cards[cur]:
                    stack.append(cur + ("river:" + card_str(card),))
        return count


def replay_chips(index: TreeIndex, hist: Tuple[str, ...]) -> Tuple[float, float]:
    """Contribution pair after replaying ``hist`` from the root."""
    chips = (0.0, 0.0)
    prefix = ()  # type: Tuple[str, ...]
    for elem in hist:
        if elem.startswith("river:"):
            continue
        node = index.node_at.get(prefix)
        if node is None:
            raise ValueError("no player node at %r" % (list(prefix),))
        chips = apply_action(chips, node[0], elem)
        prefix = prefix + (elem,)
    return chips


def dealt_board(spot_board: Sequence[str], hist: Sequence[str]) -> Tuple[int, ...]:
    """Board card ids at ``hist``: spot board plus dealt river cards."""
    ids = list(parse_cards(spot_board))
    for elem in hist:
        if elem.startswith("river:"):
            ids.append(card_id(elem[6:]))
    return tuple(ids)


def valid_mass(reach0: Dict[str, float], reach1: Dict[str, float]) -> float:
    """Sum of reach products over non-conflicting (actually dealt) pairs."""
    mass = 0.0
    for h0, r0 in reach0.items():
        c0t = combo_tuple(h0)
        inner = 0.0
        for h1, r1 in reach1.items():
            c1t = combo_tuple(h1)
            if combos_conflict(c0t, c1t):
                continue
            inner += r1
        mass += r0 * inner
    return mass


def showdown(reach0: Dict[str, float], reach1: Dict[str, float],
             c0: float, c1: float, board_now: Tuple[int, ...], pot: float) -> float:
    """Player 0's summed gross winnings at showdown (before sunk chips)."""
    total = pot + c0 + c1
    rank1 = {}  # type: Dict[str, Tuple[int, Tuple[int, ...]]]
    contrib = 0.0
    for h0, r0 in reach0.items():
        cards0 = combo_tuple(h0)
        v0 = rank7(board_now + cards0)
        wins = ties = 0.0
        for h1, r1 in reach1.items():
            cards1 = combo_tuple(h1)
            if combos_conflict(cards0, cards1):
                continue
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


def _drop_card(reach: Dict[str, float], card: int) -> Dict[str, float]:
    return {h: r for h, r in reach.items() if card not in combo_tuple(h)}


def edge_value(index: TreeIndex, hist: Tuple[str, ...], chips: Tuple[float, float],
               reach0: Dict[str, float], reach1: Dict[str, float],
               board_now: Tuple[int, ...], pot: float) -> float:
    """Player 0's summed net payoff of the subtree at ``hist``."""
    kind = index.edge_kind(hist)
    if kind == "terminal":
        c0, c1 = chips
        return showdown(reach0, reach1, c0, c1, board_now, pot) - valid_mass(reach0, reach1) * (
            c0 + pot / 2.0
        )
    if kind == "chance":
        # The river is uniform over the 52 - len(board) - 4 remaining cards;
        # combos holding the dealt card are filtered out of both reaches.
        deck_left = 52 - len(board_now) - 4
        total = 0.0
        for card in index.chance_cards[hist]:
            if card in board_now:
                continue  # dead stub, unreachable
            r0 = _drop_card(reach0, card)
            r1 = _drop_card(reach1, card)
            total += (1.0 / deck_left) * walk(
                index, hist + ("river:" + card_str(card),), chips, r0, r1,
                board_now + (card,), pot,
            )
        return total
    return walk(index, hist, chips, reach0, reach1, board_now, pot)


def walk(index: TreeIndex, hist: Tuple[str, ...], chips: Tuple[float, float],
         reach0: Dict[str, float], reach1: Dict[str, float],
         board_now: Tuple[int, ...], pot: float) -> float:
    """Player 0's summed net payoff of the player node at ``hist``."""
    node = index.node_at.get(hist)
    if node is None:
        raise ValueError("no player node at %r" % (list(hist),))
    player, actions = node
    rows = index.rows_at[hist]
    reach = reach0 if player == 0 else reach1
    c0, c1 = chips
    total = 0.0
    for action in actions:
        chips_next = apply_action(chips, player, action)
        child_reach = {}
        seen = set()
        for combo, freqs in rows.items():
            seen.add(combo)
            child_reach[combo] = reach.get(combo, 0.0) * freqs.get(action, 0.0)
        for combo in reach:
            if combo not in seen:
                child_reach[combo] = 0.0
        next0, next1 = (child_reach, reach1) if player == 0 else (reach0, child_reach)
        if action == "fold":
            mass = valid_mass(next0, next1)
            total += mass * ((pot / 2.0 + c1) if player == 1 else (-pot / 2.0 - c0))
            continue
        total += edge_value(index, hist + (action,), chips_next, next0, next1, board_now, pot)
    return total


def initial_reaches(range_weights0: Dict[str, float], range_weights1: Dict[str, float],
                    blocked: Tuple[int, ...]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Reach dicts at the root: range weights minus board/dead combos."""
    blocked_set = set(blocked)
    init0 = {c: w for c, w in range_weights0.items() if not (set(combo_tuple(c)) & blocked_set)}
    init1 = {c: w for c, w in range_weights1.items() if not (set(combo_tuple(c)) & blocked_set)}
    return init0, init1


def root_ev(index: TreeIndex, range_weights0: Dict[str, float],
            range_weights1: Dict[str, float], board_ids: Tuple[int, ...],
            dead_ids: Tuple[int, ...], pot: float) -> float:
    """Player 0's normalized net EV — comparable to ``profile_value_p0``."""
    init0, init1 = initial_reaches(range_weights0, range_weights1, board_ids + dead_ids)
    norm = valid_mass(init0, init1)
    if norm <= 0:
        raise ValueError("no dealt pairs: ranges fully blocked")
    return walk(index, (), (0.0, 0.0), init0, init1, board_ids, pot) / norm


def reach_at(index: TreeIndex, hist: Tuple[str, ...],
             range_weights0: Dict[str, float], range_weights1: Dict[str, float],
             blocked: Tuple[int, ...]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Both players' reaches at ``hist`` (``w(c)·own_reach(c)`` per combo)."""
    reach0, reach1 = initial_reaches(range_weights0, range_weights1, blocked)
    prefix = ()  # type: Tuple[str, ...]
    for elem in hist:
        if elem.startswith("river:"):
            dealt = card_id(elem[6:])
            reach0 = _drop_card(reach0, dealt)
            reach1 = _drop_card(reach1, dealt)
            continue
        node = index.node_at.get(prefix)
        if node is None:
            raise ValueError("no player node at %r" % (list(prefix),))
        player, actions = node
        if elem not in actions:
            raise ValueError("action %r not offered at %r" % (elem, list(prefix)))
        rows = index.rows_at[prefix]
        reach = reach0 if player == 0 else reach1
        child_reach = {}
        seen = set()
        for combo, freqs in rows.items():
            seen.add(combo)
            child_reach[combo] = reach.get(combo, 0.0) * freqs.get(elem, 0.0)
        for combo in reach:
            if combo not in seen:
                child_reach[combo] = 0.0
        if player == 0:
            reach0 = child_reach
        else:
            reach1 = child_reach
        prefix = prefix + (elem,)
    return reach0, reach1


def node_action_evs(index: TreeIndex, hist: Tuple[str, ...],
                    reach0: Dict[str, float], reach1: Dict[str, float],
                    board_now: Tuple[int, ...], pot: float,
                    chips: Optional[Tuple[float, float]] = None) -> Dict[str, Dict[str, float]]:
    """Per-combo per-action conditional EVs for the acting player at ``hist``.

    ``EV(c, a)`` = the acting player's child-subtree value with
    ``reach_p = {c: 1}`` divided by the opponent's compatible reach mass
    (mirrors ``scripts/phase8/xeval.py::node_action_evs``).
    """
    node = index.node_at.get(hist)
    if node is None:
        raise ValueError("no player node at %r" % (list(hist),))
    player, actions = node
    chips = replay_chips(index, hist) if chips is None else chips
    c0, c1 = chips
    reach = reach0 if player == 0 else reach1
    opp_reach = reach1 if player == 0 else reach0
    evs = {}  # type: Dict[str, Dict[str, float]]
    for combo in index.rows_at[hist]:
        if reach.get(combo, 0.0) <= 0.0:
            continue
        ct = combo_tuple(combo)
        opp_mass = sum(
            r for d, r in opp_reach.items()
            if ct[0] not in combo_tuple(d) and ct[1] not in combo_tuple(d)
        )
        if opp_mass <= 0.0:
            continue
        single0 = {combo: 1.0} if player == 0 else reach0
        single1 = {combo: 1.0} if player == 1 else reach1
        row = {}
        for action in actions:
            chips_next = apply_action(chips, player, action)
            if action == "fold":
                v = (pot / 2.0 + c1) if player == 1 else (-pot / 2.0 - c0)
                row[action] = -v if player == 1 else v
                continue
            v = edge_value(index, hist + (action,), chips_next, single0, single1, board_now, pot)
            row[action] = (-v if player == 1 else v) / opp_mass
        evs[combo] = row
    return evs


def equity(combo: str, opp_reach: Dict[str, float], board_now: Tuple[int, ...]) -> float:
    """Showdown equity of ``combo`` vs ``opp_reach`` on ``board_now``.

    On a 4-card (turn) board the remaining river card is uniform over cards
    seen in neither hand nor the board.
    """
    ct = combo_tuple(combo)
    opp_mass = 0.0
    acc = 0.0
    board = tuple(board_now)
    v0 = rank7(board + ct) if len(board) >= 5 else None
    for d, r in opp_reach.items():
        dt = combo_tuple(d)
        if combos_conflict(ct, dt):
            continue
        opp_mass += r
        if v0 is not None:
            v1 = rank7(board + dt)
            acc += r * (1.0 if v0 > v1 else 0.5 if v0 == v1 else 0.0)
        else:
            wins = ties = 0.0
            for card in range(52):
                if card in board or card in ct or card in dt:
                    continue
                a = rank7(board + (card,) + ct)
                b = rank7(board + (card,) + dt)
                if a > b:
                    wins += 1.0
                elif a == b:
                    ties += 1.0
            cards_left = 52 - len(board) - 4
            acc += r * (wins + 0.5 * ties) / cards_left
    return acc / opp_mass if opp_mass > 0.0 else 0.0


def parse_history(history: Any) -> Tuple[str, ...]:
    """History query value -> element tuple.

    Accepts a comma-separated string (``"check,b10"``), a pipe-sectioned
    string (``"check,b10|2c|check"``, the key wire format), or a list of
    elements. Card-like sections become ``river:<card>``.
    """
    if history is None:
        return ()
    if isinstance(history, (list, tuple)):
        parts = [str(p) for p in history]
    else:
        parts = []
        for section in str(history).split("|"):
            parts.extend(p for p in section.split(",") if p.strip())
    out = []
    card_re = re.compile(r"^[2-9TJQKA][cdhs]$")
    for part in parts:
        part = part.strip()
        if part.startswith("river:"):
            card = part[6:]
            if not card_re.match(card):
                raise ValueError("bad dealt card %r" % part)
            out.append("river:" + card)
        elif card_re.match(part):
            out.append("river:" + part)
        else:
            out.append(part)
    return tuple(out)
