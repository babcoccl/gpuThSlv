"""Vectorized (numpy) subtree EVs for the ``/node`` endpoint.

A drop-in replacement for ``evwalk.node_action_evs``: same per-combo
per-action conditional EVs, computed in one pass over the subtree instead
of one walk per (combo, action).

Idea: with the acting player's reach held to a single combo ``{c: 1}``,
``walk`` is linear in that combo's reach and its only per-combo dependence
is (a) the combo's own frequencies at its own nodes and (b) blocker/rank
masks against the opponent's reach vector at terminals and folds. Both
vectorize over all combos at once — the walk is run with ``own`` fixed to
one player and a length-1326 vector ``V`` indexed by combo gid:

* own node: ``V[c] += freq(c, a) · V_child_a[c]`` — elementwise;
* opp node: the child is re-evaluated with ``r_opp ⊙ freq(·, a)`` — the
  same updated opponent vector for every ``c``;
* terminal showdown: ``wins/ties/compat`` per combo come from rank-sorted
  prefix sums plus per-card corrections (``Σ_{d∋k} r_d``);
* fold: ``compat(c, r_opp) · payoff``.

``combo`` strings map to dense ids over the 1326 two-card combos; rank
levels and per-card combo orderings are cached per board.

``all_node_evs`` runs the same walk twice (``own`` = 0 and 1) and records
the conditional EVs at every node — what the scheduler persists as
``strategy-<id>.evs.npz`` at job completion so a first-hit ``/node`` is a
gather, not a walk.

API venv only (numpy); ``holdem_solver.protocol.evwalk`` stays the shared
pure-Python reference.
"""

from __future__ import annotations

import itertools
from functools import lru_cache
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from holdem_solver.protocol.cards import card_str
from holdem_solver.protocol.evwalk import (
    TreeIndex,
    apply_action,
    combo_tuple,
    initial_reaches,
    rank7,
    replay_chips,
)

_ALL = list(itertools.combinations(range(52), 2))  # 1326 combos, (a,b) a<b
_GID = {c: i for i, c in enumerate(_ALL)}
_CARD_MEMBERS = [
    np.array([i for i, c in enumerate(_ALL) if k in c], dtype=np.int64)
    for k in range(52)
]
_CARD_MASK = np.zeros((52, len(_ALL)))
for _k in range(52):
    _CARD_MASK[_k, _CARD_MEMBERS[_k]] = 1.0

_N = len(_ALL)
_CARD_A = np.array([c[0] for c in _ALL], dtype=np.int64)
_CARD_B = np.array([c[1] for c in _ALL], dtype=np.int64)


def _gid(combo: str) -> int:
    return _GID[tuple(sorted(combo_tuple(combo)))]


@lru_cache(maxsize=64)
def _levels(board: Tuple[int, ...]):
    """Per board: rank level per combo and per-card combos sorted by level."""
    ranks = [rank7(board + c) for c in _ALL]
    order = sorted(range(_N), key=lambda i: ranks[i])
    lvl = np.empty(_N, dtype=np.int64)
    cur = -1
    prev = None
    for i in order:
        if ranks[i] != prev:
            cur += 1
            prev = ranks[i]
        lvl[i] = cur
    card_order = np.empty((52, 51), dtype=np.int64)
    card_lvl = np.empty((52, 51), dtype=np.int64)
    for k in range(52):
        members = _CARD_MEMBERS[k]
        srt = members[np.argsort(lvl[members], kind="stable")]
        card_order[k] = srt
        card_lvl[k] = lvl[srt]
    return lvl, card_order, card_lvl


def _compat_all(r_opp: np.ndarray) -> np.ndarray:
    """``compat(c, r)`` for all 1326 combos: ``Σ_d r_d·I(no shared card)``."""
    card_mass = _CARD_MASK @ r_opp
    return r_opp.sum() - card_mass[_CARD_A] - card_mass[_CARD_B] + r_opp


def _freq_vec(rows: Dict[str, Dict[str, float]], action: str) -> np.ndarray:
    """The node's per-combo frequency for ``action`` as a 1326-vector."""
    f = np.zeros(_N)
    for c, freqs in rows.items():
        f[_gid(c)] = freqs.get(action, 0.0)
    return f


def _evs_walk(
    index: TreeIndex,
    hist: Tuple[str, ...],
    chips: Tuple[float, float],
    board: Tuple[int, ...],
    pot: float,
    own: int,
    r_opp: np.ndarray,
    record: Optional[
        Callable[[Tuple[str, ...], Dict[str, np.ndarray], np.ndarray], None]
    ] = None,
) -> np.ndarray:
    """``edge_value`` at ``hist`` with own reach ``{c: 1}``, for every combo
    ``c`` at once; returns the p0-summed-payoff vector indexed by gid.

    ``record(hist, children, compat)`` fires once at each node where
    ``player == own``: ``children`` maps each non-fold action to its child
    vector, ``compat`` is ``compat(c, r_opp)`` at the node.
    """
    kind = index.edge_kind(hist)
    if kind == "terminal":
        lvl, card_order, card_lvl = _levels(board)
        mass_lvl = np.bincount(lvl, weights=r_opp, minlength=int(lvl.max()) + 1)
        cum_below = np.concatenate(([0.0], np.cumsum(mass_lvl)))
        below = cum_below[lvl].copy()
        eq = mass_lvl[lvl].copy()
        comp = _compat_all(r_opp)
        card_cum = np.stack(
            [np.cumsum(r_opp[card_order[k]]) for k in range(52)]
        )
        for g in range(_N):
            l = int(lvl[g])
            ca, cb = _ALL[g]
            for k in (ca, cb):
                lo = int(np.searchsorted(card_lvl[k], l, side="left"))
                hi = int(np.searchsorted(card_lvl[k], l, side="right"))
                lt = card_cum[k, lo - 1] if lo > 0 else 0.0
                le = card_cum[k, hi - 1] if hi > 0 else 0.0
                below[g] -= lt
                eq[g] -= le - lt
        eq += r_opp
        above = comp - below - eq
        total = pot + chips[0] + chips[1]
        sunk = comp * (chips[0] + pot / 2.0)
        if own == 0:
            return (below + 0.5 * eq) * total - sunk
        return (above + 0.5 * eq) * total - sunk
    if kind == "chance":
        deck_left = 52 - len(board) - 4
        v = np.zeros(_N)
        for card in index.chance_cards[hist]:
            if card in board:
                continue  # dead stub, unreachable
            keep = 1.0 - _CARD_MASK[card]
            child = _evs_walk(
                index, hist + ("river:" + card_str(card),), chips,
                board + (card,), pot, own, r_opp * keep, record,
            )
            child *= keep  # own combos holding the dealt card reach nothing
            v += child / deck_left
        return v
    pl, acts = index.node_at[hist]
    rows = index.rows_at[hist]
    v = np.zeros(_N)
    c0, c1 = chips
    comp = _compat_all(r_opp) if pl == own else None
    children: Dict[str, np.ndarray] = {}
    for a in acts:
        chips_next = apply_action(chips, pl, a)
        if pl == own:
            f = _freq_vec(rows, a)
            if a == "fold":
                payoff = (pot / 2.0 + c1) if pl == 1 else (-pot / 2.0 - c0)
                v += f * comp * payoff
            else:
                child = _evs_walk(
                    index, hist + (a,), chips_next, board, pot, own, r_opp, record
                )
                children[a] = child
                v += f * child
        else:
            r_next = r_opp * _freq_vec(rows, a)
            if a == "fold":
                payoff = (pot / 2.0 + c1) if pl == 1 else (-pot / 2.0 - c0)
                v += _compat_all(r_next) * payoff
            else:
                v += _evs_walk(
                    index, hist + (a,), chips_next, board, pot, own, r_next, record
                )
    if comp is not None and record is not None:
        record(hist, children, comp)
    return v


def node_action_evs(
    index: TreeIndex,
    hist: Tuple[str, ...],
    reach0: Dict[str, float],
    reach1: Dict[str, float],
    board_now: Tuple[int, ...],
    pot: float,
    chips: Tuple[float, float] | None = None,
) -> Dict[str, Dict[str, float]]:
    """``evwalk.node_action_evs`` equivalent — one vectorized pass."""
    node = index.node_at.get(hist)
    if node is None:
        raise ValueError("no player node at %r" % (list(hist),))
    player, actions = node
    chips = replay_chips(index, hist) if chips is None else chips
    opp = reach1 if player == 0 else reach0
    own_reach = reach0 if player == 0 else reach1
    r0 = np.zeros(_N)
    for d, r in opp.items():
        r0[_gid(d)] = r
    comp = _compat_all(r0)
    rows = index.rows_at[hist]
    sign = -1.0 if player == 1 else 1.0
    evs: Dict[str, Dict[str, float]] = {}
    for a in actions:
        chips_next = apply_action(chips, player, a)
        if a == "fold":
            v_fold = (pot / 2.0 + chips[1]) if player == 1 else (-pot / 2.0 - chips[0])
            col = np.full(_N, sign * v_fold)
        else:
            col = sign * _evs_walk(
                index, hist + (a,), chips_next, board_now, pot, player, r0
            )
        for c in rows:
            if own_reach.get(c, 0.0) <= 0.0:
                continue
            m = comp[_gid(c)]
            if m <= 0.0:
                continue
            evs.setdefault(c, {})[a] = (
                float(col[_gid(c)]) if a == "fold" else float(col[_gid(c)] / m)
            )
    return evs


def all_node_evs(
    index: TreeIndex,
    board0: Tuple[int, ...],
    pot: float,
    range_weights0: Dict[str, float],
    range_weights1: Dict[str, float],
    blocked: Tuple[int, ...],
) -> Tuple[
    Dict[Tuple[int, Tuple[str, ...]], Dict[str, Dict[str, float]]],
    Dict[Tuple[int, Tuple[str, ...]], Dict[str, float]],
]:
    """Conditional per-combo per-action EVs for EVERY player node — the
    ``node_action_evs`` output for every history in one pair of passes.

    Returns ``(evs, opp_mass)``: ``evs[(player, hist)][combo][action]`` is
    the same conditional EV ``node_action_evs`` reports; ``opp_mass`` is
    the opponent's compatible reach mass at that node (a combo with
    ``opp_mass <= 0`` or no own reach is omitted, mirroring the reference).
    """
    reach0, reach1 = initial_reaches(range_weights0, range_weights1, blocked)
    evs: Dict[Tuple[int, Tuple[str, ...]], Dict[str, Dict[str, float]]] = {}
    masses: Dict[Tuple[int, Tuple[str, ...]], Dict[str, float]] = {}
    for own in (0, 1):
        opp = reach1 if own == 0 else reach0
        own_reach = reach0 if own == 0 else reach1
        r0 = np.zeros(_N)
        for d, r in opp.items():
            r0[_gid(d)] = r
        sign = -1.0 if own == 1 else 1.0

        def record(
            hist: Tuple[str, ...], children: Dict[str, np.ndarray],
            comp: np.ndarray, _own: int = own, _sign: float = sign,
            _own_reach: Dict[str, float] = own_reach,
        ) -> None:
            node = index.node_at[hist]
            rows = index.rows_at[hist]
            chips_now = replay_chips(index, hist)
            table = evs.setdefault((_own, hist), {})
            mass_table = masses.setdefault((_own, hist), {})
            for a in node[1]:
                if a == "fold":
                    v_fold = (
                        (pot / 2.0 + chips_now[1]) if _own == 1
                        else (-pot / 2.0 - chips_now[0])
                    )
                    col = np.full(_N, _sign * v_fold)
                else:
                    col = _sign * children[a]
                for c in rows:
                    if _own_reach.get(c, 0.0) <= 0.0:
                        continue
                    m = comp[_gid(c)]
                    if m <= 0.0:
                        continue
                    mass_table[c] = float(m)
                    table.setdefault(c, {})[a] = (
                        float(col[_gid(c)]) if a == "fold" else float(col[_gid(c)] / m)
                    )

        _evs_walk(
            index, (), replay_chips(index, ()), board0, pot, own, r0, record
        )
    return evs, masses
