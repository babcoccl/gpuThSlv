"""Aggregate parsed nodes (ours or TexasSolver's) into 169 hand-class grids.

Mirrors ``holdem_solver.api.strategy.hand_grid``: per (player, history)
decision point, each class's frequency per action is the unweighted mean over
its combos present at that point. Python 3.9 safe.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

RANKS = "AKQJT98765432"
_CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")


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


def to_grid(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{player, history, actions, strategy:{combo:{action:freq}}}]
    -> hand_grid-shaped [{player, history, actions, grid}]."""
    groups = {}  # type: Dict[Tuple[int, Tuple[str, ...]], Dict[str, Any]]
    for node in nodes:
        hist = tuple(node["history"])
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
        grid = []
        for cls in order:
            cell = group["cells"].get(cls)
            combos = 0 if cell is None else cell["combos"]
            freqs = {} if not combos else {n: cell["sums"].get(n, 0.0) / combos for n in names}
            grid.append({"hand_class": cls, "combos": combos, "frequencies": freqs})
        out.append(
            {
                "player": who,
                "history": list(hist),
                "actions": [{"name": n, "label": n} for n in names],
                "grid": grid,
            }
        )
    return out
