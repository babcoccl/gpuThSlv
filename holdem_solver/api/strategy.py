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
