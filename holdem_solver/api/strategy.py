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


def _canon_combo(card_a: str, card_b: str) -> str:
    """Two card strings -> engine combo naming (low card id first)."""
    id_a = 4 * RANKS.index(card_a[0]) + "sdch".index(card_a[1])
    id_b = 4 * RANKS.index(card_b[0]) + "sdch".index(card_b[1])
    return card_a + card_b if id_a <= id_b else card_b + card_a


def _class_combo_names() -> dict[str, list[str]]:
    """``{class: [combo_str, ...]}`` — the 169 classes expanded to combos."""
    out = {}
    for i, hi in enumerate(RANKS):
        for j, lo in enumerate(RANKS):
            if i == j:
                out[hi + lo] = [
                    _canon_combo(hi + "sdch"[a], hi + "sdch"[b])
                    for a in range(4)
                    for b in range(a + 1, 4)
                ]
            elif i < j:
                out[hi + lo + "s"] = [
                    _canon_combo(hi + suit, lo + suit) for suit in "sdch"
                ]
            else:
                out[lo + hi + "o"] = [
                    _canon_combo(lo + "sdch"[a], hi + "sdch"[b])
                    for a in range(4)
                    for b in range(4)
                    if a != b
                ]
    return out


_CLASS_COMBOS = _class_combo_names()


def _combo_blocked(hand: str, blocked: set[str]) -> bool:
    """``hand`` shares a card with ``blocked`` (board / dealt / dead cards)."""
    return hand[:2] in blocked or hand[2:] in blocked


def _own_reach(
    by_node: dict[tuple[int, tuple[str, ...]], dict[str, dict[str, float]]],
    who: int,
    hand: str,
    hist: tuple[str, ...],
) -> float:
    """Product of the solved-strategy frequencies with which ``who``'s own
    actions along ``hist`` were taken holding ``hand`` (0 when the combo
    cannot reach the node)."""
    reach = 1.0
    for i, step in enumerate(hist):
        row = by_node.get((who, hist[:i]))
        if row is None:
            continue  # ``who`` did not act at this prefix (opponent or chance)
        freqs = row.get(hand)
        if freqs is None:
            return 0.0
        reach *= freqs.get(step, 0.0)
        if reach == 0.0:
            return 0.0
    return reach


def _node_weights(
    by_node: dict[tuple[int, tuple[str, ...]], dict[str, dict[str, float]]],
    who: int,
    hist: tuple[str, ...],
    range_w: dict[str, float],
    opp_w: dict[str, float],
    blocked: set[str],
) -> dict[str, float]:
    """Per-combo weight = ``w(c) · own_reach(c) · opp_mass(c)``.

    ``opp_mass(c)`` is the opponent range's reach mass over combos that do
    not conflict with ``c`` (blocker-aware); a node-level constant cancels in
    the normalized frequencies but card removal does not.
    """
    own = {}
    for combo, w in range_w.items():
        if w <= 0.0 or _combo_blocked(combo, blocked):
            continue
        reach = _own_reach(by_node, who, combo, hist)
        if reach > 0.0:
            own[combo] = w * reach
    if not own:
        return {}
    opp_reach = {}  # type: dict[str, float]
    for combo, w in opp_w.items():
        if w <= 0.0 or _combo_blocked(combo, blocked):
            continue
        reach = _own_reach(by_node, 1 - who, combo, hist)
        if reach > 0.0:
            opp_reach[combo] = w * reach
    by_card: dict[str, set[str]] = {}
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


def hand_grid(
    mapped: dict,
    player: int | None = None,
    history: list[str] | None = None,
    ranges: dict[int, dict[str, float]] | None = None,
    blocked: set[str] | None = None,
) -> dict:
    """Aggregate a ``map_strategy`` result into 169-class grids, one per
    decision point (player + action history).

    When ``ranges`` (``{player: {combo: range weight}}``) is supplied, each
    class's frequency per action is the reach-weighted mean over its combos:

        weight(c) = w(c) · own_reach(c, node) · opp_mass(c, node)

    where ``own_reach`` is the product of the acting player's own action
    frequencies along the history and ``opp_mass`` sums the opponent's reach
    over combos not sharing a card with ``c``. Combos containing a board,
    dealt, or ``blocked`` card contribute zero. Classes with no weighted
    combo at that point have ``combos=0`` and empty frequencies; ``reach`` is
    the class's total weight. Without ``ranges`` the aggregation falls back
    to the unweighted per-combo mean.
    """
    groups: dict[tuple[int, tuple[str, ...]], dict[str, Any]] = {}
    by_node: dict[tuple[int, tuple[str, ...]], dict[str, dict[str, float]]] = {}
    for info in mapped["infosets"]:
        if info["player"] is None or info["hand"] is None:
            continue
        hist = tuple(info["history"] or ())
        freqs = {
            a["name"]: a["frequency"] for a in info["actions"] if a["name"] is not None
        }
        by_node.setdefault((info["player"], hist), {})[info["hand"]] = freqs
        if player is not None and info["player"] != player:
            continue
        if history is not None and list(hist) != history:
            continue
        cls = hand_class(info["hand"])
        if cls is None:
            continue
        group = groups.setdefault((info["player"], hist), {"actions": {}, "cells": {}})
        cell = group["cells"].setdefault(cls, {"combos": 0, "sums": {}, "reach": 0.0})
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
        weights = None
        if ranges is not None:
            # river cards dealt inside the history block combos holding them
            blocked_now = set(blocked or ()) | {h[6:] for h in hist if h.startswith("river:")}
            weights = _node_weights(by_node, who, hist, ranges.get(who, {}), ranges.get(1 - who, {}), blocked_now)
        grid = []
        for cls in order:
            cell = group["cells"].get(cls)
            if weights is not None:
                cls_weight = 0.0
                sums = {n: 0.0 for n in names}
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
                freqs = {} if not cls_weight else {n: sums[n] / cls_weight for n in names}
                grid.append(
                    {"hand_class": cls, "combos": ncombos, "reach": cls_weight, "frequencies": freqs}
                )
            else:
                combos = 0 if cell is None else cell["combos"]
                freqs = {} if not combos else {n: cell["sums"].get(n, 0.0) / combos for n in names}
                grid.append({"hand_class": cls, "combos": combos, "reach": float(combos), "frequencies": freqs})
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


def spot_ranges(spot: dict) -> dict[int, dict[str, float]] | None:
    """``{player: {combo_str: weight}}`` from a stored spot dict, or None when
    the spot carries no parseable ranges (non-HUNL games)."""
    from holdem_solver.protocol.cards import combo_str, parse_range

    try:
        range0 = parse_range(spot.get("range0", ""))
        range1 = parse_range(spot.get("range1", ""))
    except Exception:
        return None
    return {
        0: {combo_str(combo): w for combo, w in range0.items()},
        1: {combo_str(combo): w for combo, w in range1.items()},
    }


def spot_blocked(spot: dict) -> set[str]:
    """Board + dead cards as card strings, for reach weighting."""
    from holdem_solver.protocol.cards import parse_cards, card_str

    blocked = set()
    for field in ("board", "dead_cards"):
        try:
            blocked.update(card_str(c) for c in parse_cards(spot.get(field) or ()))
        except Exception:
            pass
    return blocked
