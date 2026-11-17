"""Map engine infoset keys back to hand names and action labels.

GPU-CFR's HUNL builders name infosets ``p<player>|<hand>|<history>`` where
``<history>`` is pipe-separated street sections of comma-separated action
names. On turn spots a bare card field (e.g. ``2c``) separates the turn
history from the river history:

* river: ``p1|9d9h|check,b10`` — hand ``9d9h``, history ``check, b10``
* turn:  ``p0|4c3c|b10,call|2c|check,b10`` — turn history ``b10, call``,
  river card ``2c``, river history ``check, b10``

Action names come from the worker's ``get_strategy`` (the ``actions`` array,
aligned with ``keys``); they are ``check``, ``fold``, ``call``, and
``b<chips>`` for the HUNL builders, labelled ``"bet <chips>"`` in the
response. Keys that do not match the HUNL form (Kuhn games, ``FakeEngine``'s
``fake/NNNNN``) keep their raw key with ``player``/``hand``/``history``
unset; the response counts them in ``unmapped_keys``.
"""

from __future__ import annotations

import re
from typing import Any

_KEY_RE = re.compile(r"^p([01])\|([^|]+)\|(.*)$")
_BET_RE = re.compile(r"^b(\d+(?:\.\d+)?)$")
_CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")


def action_label(name: str) -> str:
    match = _BET_RE.match(name)
    if match:
        return "bet %s" % match.group(1)
    return name


def parse_infoset_key(key: str) -> dict[str, Any] | None:
    """``{player, hand, history}`` for a HUNL infoset key, or None."""
    match = _KEY_RE.match(key)
    if match is None:
        return None
    history = []  # type: list[str]
    for section in match.group(3).split("|"):
        if _CARD_RE.match(section):
            history.append("river:" + section)
        elif section:
            history.extend(action for action in section.split(",") if action)
    return {"player": int(match.group(1)), "hand": match.group(2), "history": history}


def map_strategy(keys: list[str], actions: list[list[str]], offsets: Any, values: Any, iteration: int) -> dict:
    """Build the strategy response from flat keys/actions/offsets/values."""
    bounds = offsets.tolist()
    infosets = []
    unmapped = 0
    for i, key in enumerate(keys):
        row = values[bounds[i] : bounds[i + 1]]
        parsed = parse_infoset_key(key)
        if parsed is None:
            unmapped += 1
        names = actions[i] if i < len(actions) else []
        infosets.append(
            {
                "key": key,
                "player": None if parsed is None else parsed["player"],
                "hand": None if parsed is None else parsed["hand"],
                "history": None if parsed is None else parsed["history"],
                "actions": [
                    {
                        "name": names[j] if j < len(names) else None,
                        "label": action_label(names[j]) if j < len(names) else None,
                        "frequency": float(value),
                    }
                    for j, value in enumerate(row.tolist())
                ],
            }
        )
    return {
        "iteration": iteration,
        "num_infosets": len(keys),
        "infosets": infosets,
        "unmapped_keys": unmapped,
    }


RANKS = "AKQJT98765432"


def hand_class(hand: str) -> str | None:
    """``AsKs`` -> ``AKs``, ``KdAc`` -> ``AKo``, ``9d9h`` -> ``99``; None if not two cards."""
    if len(hand) != 4 or not (_CARD_RE.match(hand[:2]) and _CARD_RE.match(hand[2:])):
        return None
    (r1, s1), (r2, s2) = hand[:2], hand[2:]
    if RANKS.index(r1) > RANKS.index(r2):
        r1, r2 = r2, r1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def hand_classes() -> list[str]:
    """The 169 classes in 13x13 chart order (row-major): pairs on the
    diagonal, suited above it, offsuit below it."""
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


def hand_grid(mapped: dict, player: int | None = None, history: list[str] | None = None) -> dict:
    """Aggregate a ``map_strategy`` result into 169-class grids, one per
    decision point (player + action history).

    Each class's frequency is the unweighted mean over the class's combos
    that reach the decision point (every combo in the range counts equally;
    the solver's reach probabilities are not applied). Classes with no combo
    at that point have ``combos=0`` and empty frequencies.
    """
    groups: dict[tuple[int, tuple[str, ...]], dict[str, Any]] = {}
    for info in mapped["infosets"]:
        if info["player"] is None or info["hand"] is None:
            continue
        if player is not None and info["player"] != player:
            continue
        hist = tuple(info["history"] or ())
        if history is not None and list(hist) != history:
            continue
        cls = hand_class(info["hand"])
        if cls is None:
            continue
        group = groups.setdefault((info["player"], hist), {"actions": {}, "cells": {}})
        cell = group["cells"].setdefault(cls, {"combos": 0, "sums": {}})
        cell["combos"] += 1
        for action in info["actions"]:
            name = action["name"]
            if name is None:
                continue
            group["actions"].setdefault(name, action["label"])
            cell["sums"][name] = cell["sums"].get(name, 0.0) + action["frequency"]
    order = hand_classes()
    nodes = []
    for (who, hist), group in sorted(groups.items(), key=lambda item: (len(item[0][1]), item[0])):
        names = list(group["actions"])
        grid = []
        for cls in order:
            cell = group["cells"].get(cls)
            combos = 0 if cell is None else cell["combos"]
            freqs = {} if not combos else {n: cell["sums"].get(n, 0.0) / combos for n in names}
            grid.append({"hand_class": cls, "combos": combos, "frequencies": freqs})
        nodes.append(
            {
                "player": who,
                "history": list(hist),
                "actions": [{"name": n, "label": group["actions"][n]} for n in names],
                "combos": sum(cell["combos"] for cell in group["cells"].values()),
                "grid": grid,
            }
        )
    return {"iteration": mapped["iteration"], "num_infosets": mapped["num_infosets"], "nodes": nodes}
