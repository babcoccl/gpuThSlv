"""Tree navigation and per-node values built from an exported strategy.

The exported ``strategy-<id>.npz`` encodes the whole game tree: decision
nodes are the histories present in the infoset keys, turn street-ends are
chance positions followed by ``river:<card>`` elements, and edges with no
continuation are terminals. ``TreeIndex`` (in ``protocol.evwalk``, shared
with the engine venv) rebuilds that structure; this module shapes the REST
responses for ``GET /v1/solves/{id}/tree`` and ``/node``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from holdem_solver.protocol.cards import card_str, parse_cards
from holdem_solver.protocol.evwalk import (
    TreeIndex,
    combo_tuple,
    dealt_board,
    equity,
    node_action_evs,
    parse_history,
    reach_at,
    replay_chips,
)

from .strategy import (
    _CLASS_COMBOS,
    _node_weights,
    action_label,
    hand_class,
    parse_infoset_key,
    spot_blocked,
    spot_ranges,
)

# Cap on combo-node visits for a /node EV request (defends pure-Python cost).
DEFAULT_MAX_EVAL_WORK = 5_000_000


def load_index(path: str) -> tuple[TreeIndex, int]:
    """``(TreeIndex, iteration)`` from a ``strategy-<id>.npz`` file."""
    data = np.load(path, allow_pickle=False)
    keys = [key.decode("utf-8") for key in data["keys"].tolist()]
    actions = [
        [name for name in row.decode("utf-8").split(",") if name]
        for row in data["actions"].tolist()
    ]
    bounds = data["offsets"].tolist()
    values = data["values"]
    by_node: dict[tuple[int, tuple[str, ...]], dict[str, dict[str, float]]] = {}
    unmapped = 0
    for i, key in enumerate(keys):
        parsed = parse_infoset_key(key)
        if parsed is None:
            unmapped += 1
            continue
        hist = tuple(parsed["history"])
        names = actions[i] if i < len(actions) else []
        row = values[bounds[i] : bounds[i + 1]]
        freqs = {names[j]: float(v) for j, v in enumerate(row.tolist()) if j < len(names)}
        by_node.setdefault((parsed["player"], hist), {})[parsed["hand"]] = freqs
    index = TreeIndex(by_node)
    index.unmapped_keys = unmapped  # type: ignore[attr-defined]
    return index, int(data["iteration"])


def _street(spot: dict[str, Any], hist: tuple[str, ...]) -> str:
    if any(elem.startswith("river:") for elem in hist):
        return "river"
    try:
        return "turn" if len(parse_cards(spot.get("board") or ())) == 4 else "river"
    except Exception:
        return "river"


def _labels(actions: list[str]) -> list[dict[str, str]]:
    return [{"name": name, "label": action_label(name)} for name in actions]


def tree_node(index: TreeIndex, spot: dict[str, Any], hist: tuple[str, ...]) -> dict[str, Any]:
    """The ``/tree`` response for one node or chance position."""
    pot = float(spot.get("pot", 0.0))
    stack = float(spot.get("stack", 0.0))
    chips = replay_chips(index, hist)
    board = dealt_board(spot.get("board") or (), hist)
    kind = index.edge_kind(hist)
    node = index.node_at.get(hist)
    base: dict[str, Any] = {
        "history": list(hist),
        "street": _street(spot, hist),
        "pot": pot + chips[0] + chips[1],
        "stacks": {"p0": stack - chips[0], "p1": stack - chips[1]},
        "board": [card_str(c) for c in board],
        "is_chance": kind == "chance",
        "player": node[0] if node else None,
    }
    if kind == "chance":
        cards = sorted(index.chance_cards[hist])
        base["dealt_cards"] = [card_str(c) for c in cards]
        base["children"] = [
            {"edge": card_str(c), "kind": "player", "history": list(hist) + ["river:" + card_str(c)]}
            for c in cards
        ]
        return base
    actions = node[1] if node else []
    base["actions"] = _labels(actions)
    children = []
    for action in actions:
        child_hist = hist + (action,)
        child_kind = index.edge_kind(child_hist)
        entry: dict[str, Any] = {"edge": action, "kind": child_kind}
        if child_kind == "terminal":
            entry["outcome"] = "fold" if action == "fold" else "showdown"
        else:
            entry["history"] = list(child_hist)
            if child_kind == "player":
                child = index.node_at[child_hist]
                entry["player"] = child[0]
                entry["actions"] = _labels(child[1])
        children.append(entry)
    base["children"] = children
    return base


def node_values(
    index: TreeIndex,
    spot: dict[str, Any],
    hist: tuple[str, ...],
    *,
    combos: bool = False,
    max_work: int = DEFAULT_MAX_EVAL_WORK,
) -> dict[str, Any]:
    """The ``/node`` response: frequencies, per-action EVs, hand EV, equity,
    and both players' reaches at ``hist``.

    Frequencies use the validated ``w(c)·own_reach(c)·opp_mass(c)`` weights
    (``_node_weights``). ``EV(c, a)`` is the acting player's child-subtree
    value with ``reach_p = {c: 1}`` over the opponent's compatible reach
    mass; a hand's EV is ``Σ_a σ(c, a)·EV(c, a)``; class-level EVs/equities
    are reach-weighted means (``w·own_reach``). Raises ``ValueError`` when the
    work estimate exceeds ``max_work`` combo-node visits.
    """
    node = index.node_at.get(hist)
    if node is None:
        raise KeyError("no player node at history")
    player, actions = node
    rows = index.rows_at[hist]
    work = index.descendants(hist) * len(rows)
    if work > max_work:
        raise ValueError(
            "node evaluation needs %d combo-node visits (limit %d); narrow the spot or raise HOLDEM_NODE_EVAL_MAX_WORK"
            % (work, max_work)
        )
    ranges = spot_ranges(spot)
    if ranges is None:
        raise ValueError("spot carries no parseable ranges")
    w0, w1 = ranges[0], ranges[1]
    blocked_ids = tuple(
        parse_cards(spot.get("board") or ()) + parse_cards(spot.get("dead_cards") or ())
    )
    reach0, reach1 = reach_at(index, hist, w0, w1, blocked_ids)
    board_now = dealt_board(spot.get("board") or (), hist)
    pot = float(spot.get("pot", 0.0))
    evs = node_action_evs(index, hist, reach0, reach1, board_now, pot)

    blocked_strs = spot_blocked(spot) | {elem[6:] for elem in hist if elem.startswith("river:")}
    weights = _node_weights(index.by_node, player, hist, w0 if player == 0 else w1,
                            w1 if player == 0 else w0, blocked_strs)
    own_reach = reach0 if player == 0 else reach1
    opp_reach = reach1 if player == 0 else reach0

    def combo_row(combo: str) -> dict[str, Any]:
        freqs = rows.get(combo) or {}
        action_evs = evs.get(combo) or {}
        ev = sum(freqs.get(a, 0.0) * v for a, v in action_evs.items())
        return {
            "combo": combo,
            "hand_class": hand_class(combo),
            "reach": own_reach.get(combo, 0.0),
            "weight": weights.get(combo, 0.0),
            "frequencies": {a: freqs.get(a, 0.0) for a in actions},
            "action_evs": {a: action_evs.get(a) for a in actions},
            "ev": ev,
            "equity": equity(combo, opp_reach, board_now),
        }

    if combos:
        hands = [combo_row(c) for c in sorted(rows) if own_reach.get(c, 0.0) > 0.0 or weights.get(c, 0.0) > 0.0]
    else:
        grouped: dict[str, dict[str, Any]] = {}
        for combo in sorted(rows):
            cls = hand_class(combo) or "?"
            cell = grouped.setdefault(
                cls, {"hand_class": cls, "combos": 0, "reach": 0.0, "weight": 0.0,
                      "sums": {a: 0.0 for a in actions}, "ev_sums": {a: 0.0 for a in actions},
                      "ev": 0.0, "equity": 0.0}
            )
            row = combo_row(combo)
            w = weights.get(combo, 0.0)     # freq weights: w·own_reach·opp_mass
            r = row["reach"]                 # ev/equity weights: w·own_reach
            cell["combos"] += 1
            cell["reach"] += r
            cell["weight"] += w
            for a in actions:
                cell["sums"][a] += w * row["frequencies"][a]
                if row["action_evs"].get(a) is not None:
                    cell["ev_sums"][a] += r * row["action_evs"][a]
            cell["ev"] += r * row["ev"]
            cell["equity"] += r * row["equity"]
        hands = []
        for cls, cell in sorted(grouped.items()):
            hands.append({
                "hand_class": cls,
                "combos": cell["combos"],
                "reach": cell["reach"],
                "weight": cell["weight"],
                "frequencies": ({a: cell["sums"][a] / cell["weight"] for a in actions}
                                if cell["weight"] > 0 else {a: None for a in actions}),
                "action_evs": ({a: cell["ev_sums"][a] / cell["reach"] for a in actions}
                               if cell["reach"] > 0 else {a: None for a in actions}),
                "ev": cell["ev"] / cell["reach"] if cell["reach"] > 0 else None,
                "equity": cell["equity"] / cell["reach"] if cell["reach"] > 0 else None,
            })

    def range_list(reach: dict[str, float], weights_range: dict[str, float]) -> list[dict[str, Any]]:
        total = sum(reach.values())
        if combos:
            return [
                {"combo": c, "hand_class": hand_class(c), "reach": r, "share": r / total if total else 0.0}
                for c, r in sorted(reach.items()) if r > 0.0
            ]
        by_class: dict[str, float] = {}
        for c, r in reach.items():
            if r <= 0.0:
                continue
            cls = hand_class(c) or "?"
            by_class[cls] = by_class.get(cls, 0.0) + r
        return [
            {"hand_class": cls, "reach": r, "share": r / total if total else 0.0,
             "combos": sum(1 for c in _CLASS_COMBOS.get(cls, ()) if reach.get(c, 0.0) > 0.0)}
            for cls, r in sorted(by_class.items())
        ]

    return {
        "history": list(hist),
        "street": _street(spot, hist),
        "player": player,
        "pot": pot + sum(replay_chips(index, hist)),
        "stacks": dict(
            zip(("p0", "p1"), (float(spot.get("stack", 0.0)) - c for c in replay_chips(index, hist)))
        ),
        "board": [card_str(c) for c in board_now],
        "actions": _labels(actions),
        "ranges": {"p0": range_list(reach0, w0), "p1": range_list(reach1, w1)},
        "hands": hands,
    }
