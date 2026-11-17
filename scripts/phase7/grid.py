"""Aggregate parsed nodes (ours or TexasSolver's) into 169 hand-class grids.

Mirrors ``holdem_solver.api.strategy.hand_grid``: per (player, history)
decision point, each class's frequency per action is a mean over its combos —
reach-weighted when ``ranges`` is given, unweighted otherwise. Python 3.9 safe.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

RANKS = "AKQJT98765432"
_CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")
_SUITS = "sdch"


def hand_class(hand: str) -> Optional[str]:
    """``"AsKs"`` -> ``"AKs"``, ``"KdAc"`` -> ``"AKo"``, ``"9d9h"`` -> ``"99"``."""
    if len(hand) != 4 or not (_CARD_RE.match(hand[:2]) and _CARD_RE.match(hand[2:])):
        return None
    (r1, s1), (r2, s2) = hand[:2], hand[2:]
    if RANKS.index(r1) > RANKS.index(r2):
        r1, r2 = r2, r1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def hand_classes() -> List[str]:
    """169 classes in 13x13 chart order: pairs diagonal, suited above."""
    out = []
    for i, hi in enumerate(RANKS):
        for j, lo in enumerate(RANKS):
            if i == j:
                out.append(hi + lo)
            elif i < j:
                out.append(hi + lo + "s")
            else:
                out.append(lo + hi + "o")
    return out


def _canon_combo(card_a: str, card_b: str) -> str:
    id_a = 4 * RANKS.index(card_a[0]) + _SUITS.index(card_a[1])
    id_b = 4 * RANKS.index(card_b[0]) + _SUITS.index(card_b[1])
    return card_a + card_b if id_a <= id_b else card_b + card_a


def _class_combo_names() -> Dict[str, List[str]]:
    out = {}
    for i, hi in enumerate(RANKS):
        for j, lo in enumerate(RANKS):
            if i == j:
                out[hi + lo] = [
                    _canon_combo(hi + _SUITS[a], hi + _SUITS[b])
                    for a in range(4)
                    for b in range(a + 1, 4)
                ]
            elif i < j:
                out[hi + lo + "s"] = [_canon_combo(hi + s, lo + s) for s in _SUITS]
            else:
                out[lo + hi + "o"] = [
                    _canon_combo(lo + _SUITS[a], hi + _SUITS[b])
                    for a in range(4)
                    for b in range(4)
                    if a != b
                ]
    return out


_CLASS_COMBOS = _class_combo_names()


def _combo_blocked(hand: str, blocked: Set[str]) -> bool:
    return hand[:2] in blocked or hand[2:] in blocked


def _own_reach(by_node, who: int, hand: str, hist: Tuple[str, ...]) -> float:
    """Product of own-action frequencies along ``hist`` holding ``hand``."""
    reach = 1.0
    for i, step in enumerate(hist):
        row = by_node.get((who, hist[:i]))
        if row is None:
            continue
        freqs = row.get(hand)
        if freqs is None:
            return 0.0
        reach *= freqs.get(step, 0.0)
        if reach == 0.0:
            return 0.0
    return reach


def _node_weights(by_node, who: int, hist: Tuple[str, ...],
                  range_w: Dict[str, float], opp_w: Dict[str, float],
                  blocked: Set[str]) -> Dict[str, float]:
    """``weight(c) = w(c) * own_reach(c) * opp_mass(c)``; blocked -> 0."""
    own = {}
    for combo, w in range_w.items():
        if w <= 0.0 or _combo_blocked(combo, blocked):
            continue
        reach = _own_reach(by_node, who, combo, hist)
        if reach > 0.0:
            own[combo] = w * reach
    if not own:
        return {}
    opp_reach = {}
    for combo, w in opp_w.items():
        if w <= 0.0 or _combo_blocked(combo, blocked):
            continue
        reach = _own_reach(by_node, 1 - who, combo, hist)
        if reach > 0.0:
            opp_reach[combo] = w * reach
    by_card = {}  # type: Dict[str, set]
    for combo in opp_reach:
        by_card.setdefault(combo[:2], set()).add(combo)
        by_card.setdefault(combo[2:], set()).add(combo)
    total_opp = sum(opp_reach.values())
    weights = {}
    for combo, reach in own.items():
        conflicts = by_card.get(combo[:2], set()) | by_card.get(combo[2:], set())
        opp_mass = total_opp - sum(opp_reach[d] for d in conflicts)
        if opp_mass > 0.0:
            weights[combo] = reach * opp_mass
    return weights


def to_grid(nodes: List[Dict[str, Any]],
            ranges: Optional[Dict[int, Dict[str, float]]] = None,
            blocked: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    """[{player, history, actions, strategy:{combo:{action:freq}}}]
    -> hand_grid-shaped [{player, history, actions, grid}].

    With ``ranges``={player: {combo: weight}} each class mean is weighted by
    ``w(c) * own_reach(c) * opp_mass(c)``; cells also carry ``reach`` (total
    class weight). Without ranges the mean is unweighted and ``reach`` equals
    the combo count.
    """
    groups = {}  # type: Dict[Tuple[int, Tuple[str, ...]], Dict[str, Any]]
    by_node = {}  # type: Dict[Tuple[int, Tuple[str, ...]], Dict[str, Dict[str, float]]]
    for node in nodes:
        hist = tuple(node["history"])
        row = by_node.setdefault((node["player"], hist), {})
        for combo, freqs in node.get("strategy", {}).items():
            row[combo] = freqs
        group = groups.setdefault((node["player"], hist), {"actions": {}, "cells": {}})
        for name in node["actions"]:
            group["actions"].setdefault(name, name)
        for combo, freqs in node.get("strategy", {}).items():
            cls = hand_class(combo)
            if cls is None:
                continue
            cell = group["cells"].setdefault(cls, {"combos": 0, "sums": {}})
            cell["combos"] += 1
            for name, freq in freqs.items():
                cell["sums"][name] = cell["sums"].get(name, 0.0) + freq
    order = hand_classes()
    out = []
    for (who, hist), group in sorted(groups.items(), key=lambda item: (len(item[0][1]), item[0])):
        names = list(group["actions"])
        weights = None
        if ranges is not None:
            blocked_now = set(blocked or ()) | {h[6:] for h in hist if h.startswith("river:")}
            weights = _node_weights(
                by_node, who, hist, ranges.get(who, {}), ranges.get(1 - who, {}), blocked_now
            )
        grid = []
        for cls in order:
            cell = group["cells"].get(cls)
            if weights is not None:
                cls_weight = 0.0
                sums = dict((n, 0.0) for n in names)
                ncombos = 0
                if cell is not None:
                    for combo in _CLASS_COMBOS[cls]:
                        w = weights.get(combo, 0.0)
                        if w <= 0.0:
                            continue
                        freqs = by_node[(who, hist)].get(combo)
                        if not freqs:
                            continue
                        ncombos += 1
                        cls_weight += w
                        for n in names:
                            sums[n] += w * freqs.get(n, 0.0)
                freqs = {} if not cls_weight else dict(
                    (n, sums[n] / cls_weight) for n in names
                )
                grid.append({
                    "hand_class": cls, "combos": ncombos, "reach": cls_weight,
                    "frequencies": freqs,
                })
            else:
                combos = 0 if cell is None else cell["combos"]
                freqs = {} if not combos else {
                    n: cell["sums"].get(n, 0.0) / combos for n in names
                }
                grid.append({
                    "hand_class": cls, "combos": combos, "reach": float(combos),
                    "frequencies": freqs,
                })
        out.append(
            {
                "player": who,
                "history": list(hist),
                "actions": [{"name": n, "label": n} for n in names],
                "grid": grid,
            }
        )
    return out
