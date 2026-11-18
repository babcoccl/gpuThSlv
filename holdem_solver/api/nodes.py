"""Tree navigation and per-node values built from an exported strategy.

The exported ``strategy-<id>.npz`` encodes the whole game tree: decision
nodes are the histories present in the infoset keys, turn street-ends are
chance positions followed by ``river:<card>`` elements, and edges with no
continuation are terminals. ``TreeIndex`` (in ``protocol.evwalk``, shared
with the engine venv) rebuilds that structure; this module shapes the REST
responses for ``GET /v1/solves/{id}/tree`` and ``/node``.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from holdem_solver.protocol.cards import card_str, parse_cards
from holdem_solver.protocol.evwalk import (
    TreeIndex,
    dealt_board,
    equity,
    parse_history,
    reach_at,
    replay_chips,
)

from . import evec

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

# Persisted node-EV format. v2: one chained crc32 over both payload arrays
# plus this ``format`` member; v1 files (XOR of two crc32s, no ``format``)
# are declined by load_node_evs so /node rewrites them via self-heal.
EVS_FORMAT = 2


def evs_path_for(strategy_path: str | Path) -> Path:
    """The ``strategy-<id>.evs.npz`` companion path of a strategy npz."""
    return Path(strategy_path).with_suffix(".evs.npz")


def tmp_path_for(path: str | Path) -> Path:
    """The temp file a strategy/evs export writes before ``os.replace`` —
    the single source for the name used by the exporters and the tests."""
    path = Path(path)
    return path.parent / (path.name + ".tmp")


def _evs_crc(ev: np.ndarray, mass: np.ndarray) -> int:
    """One chained crc32 over both payload arrays (format v2)."""
    return zlib.crc32(mass.tobytes(), zlib.crc32(ev.tobytes()))


def pack_node_evs(
    evs: dict,
    masses: dict,
    keys: list[str],
    actions: list,
    offsets: Any,
) -> tuple[np.ndarray, np.ndarray]:
    """Flat ``(ev, opp_mass)`` payload arrays aligned with the strategy
    npz's keys/offsets/actions — the layout ``write_node_evs`` persists."""
    bounds = list(offsets)
    flat = np.zeros(bounds[-1] if bounds else 0, dtype=np.float64)
    mass = np.zeros(len(keys), dtype=np.float64)
    for i, key in enumerate(keys):
        parsed = parse_infoset_key(key)
        if parsed is None:
            continue
        hist = tuple(parsed["history"])
        names = actions[i] if i < len(actions) else ()
        row = evs.get((parsed["player"], hist), {}).get(parsed["hand"]) or {}
        for j, name in enumerate(names):
            if bounds[i] + j < len(flat):
                flat[bounds[i] + j] = row.get(name, 0.0)
        mass[i] = masses.get((parsed["player"], hist), {}).get(parsed["hand"], 0.0)
    return flat, mass


def write_node_evs(
    evs_path: str | Path,
    keys: list[str],
    actions: list,
    offsets: Any,
    ev: np.ndarray,
    mass: np.ndarray,
    iteration: int,
    open_for_write: Any = None,
) -> None:
    """Atomically write ``strategy-<id>.evs.npz`` (format v2): temp file,
    fsync, ``os.replace``; the temp file is unlinked on any failure."""
    if open_for_write is None:
        open_for_write = lambda p: open(p, "wb")
    evs_path = Path(evs_path)
    tmp = tmp_path_for(evs_path)
    try:
        with open_for_write(str(tmp)) as handle:
            np.savez(
                handle,
                keys=np.array([key.encode("utf-8") for key in keys], dtype=np.bytes_),
                actions=np.array(
                    [",".join(names).encode("utf-8") if not isinstance(names, str)
                     else names.encode("utf-8") for names in actions],
                    dtype=np.bytes_,
                ),
                offsets=np.asarray(offsets, dtype=np.int64),
                ev=np.asarray(ev, dtype=np.float64),
                opp_mass=np.asarray(mass, dtype=np.float64),
                format=np.int64(EVS_FORMAT),
                crc32=np.int64(_evs_crc(np.asarray(ev, dtype=np.float64),
                                        np.asarray(mass, dtype=np.float64))),
                iteration=np.int64(iteration),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, evs_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


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
    index.keys_list = keys  # type: ignore[attr-defined]
    index.actions_list = actions  # type: ignore[attr-defined]
    index.offsets = bounds  # type: ignore[attr-defined]
    return index, int(data["iteration"])


def load_node_evs(path: str, keys: list[str]) -> tuple[dict, dict] | None:
    """``(evs, opp_mass)`` maps from a ``strategy-<id>.evs.npz`` companion
    file, or ``None`` when it is absent/stale.

    ``evs[hist][combo][action]`` holds the conditional EVs ``node_values``
    reports; ``opp_mass[hist][combo]`` is the opponent's compatible reach
    mass at that node. ``keys`` is the strategy npz's key list — the file is
    ignored when it does not match, so a stale companion can never serve
    values for the wrong strategy.
    """
    if not os.path.exists(path):
        return None
    try:
        data = np.load(path, allow_pickle=False)
        ev_keys = [key.decode("utf-8") for key in data["keys"].tolist()]
        if ev_keys != keys:
            return None
        offsets = data["offsets"]
        bounds = offsets.tolist()
        actions = [
            [name for name in row.decode("utf-8").split(",") if name]
            for row in data["actions"].tolist()
        ]
        ev = data["ev"]
        mass = data["opp_mass"]
        crc = data["crc32"]
        fmt = data["format"]
    except Exception:
        # truncated/garbage archives surface as OSError, KeyError,
        # ValueError, EOFError, BadZipFile, ... — all mean "not usable".
        return None
    # Schema + integrity checks: a malformed or corrupted payload declines
    # to serve rather than feed wrong values to /node.
    if ev.dtype != np.float64 or mass.dtype != np.float64:
        return None
    if len(bounds) != len(keys) + 1 or bounds[0] != 0 or bounds[-1] != len(ev):
        return None
    if np.any(np.diff(offsets) < 0):
        return None
    if len(mass) != len(keys):
        return None
    if not (np.isfinite(ev).all() and np.isfinite(mass).all()):
        return None
    if len(actions) != len(keys):
        return None
    try:
        if int(np.asarray(fmt).reshape(-1)[0]) != EVS_FORMAT:
            return None
        want = int(np.asarray(crc).reshape(-1)[0])
    except (IndexError, ValueError, TypeError, OverflowError):
        return None
    if want != _evs_crc(ev, mass):
        return None
    evs: dict[tuple[str, ...], dict[str, dict[str, float]]] = {}
    masses: dict[tuple[str, ...], dict[str, float]] = {}
    for i, key in enumerate(keys):
        parsed = parse_infoset_key(key)
        if parsed is None:
            continue
        hist = tuple(parsed["history"])
        masses.setdefault(hist, {})[parsed["hand"]] = float(mass[i])
        # Combos with zero compatible opponent mass (or no own reach) carry
        # zeros on disk; the live walk reports them as omitted cells (null
        # action EVs). Drop them here so the persisted path serves the same
        # nulls as live compute.
        if mass[i] <= 0.0:
            continue
        names = actions[i]
        row = ev[bounds[i] : bounds[i + 1]]
        evs.setdefault(hist, {})[parsed["hand"]] = {
            names[j]: float(v) for j, v in enumerate(row.tolist()) if j < len(names)
        }
    return evs, masses


def export_node_evs(
    index: TreeIndex,
    spot: dict[str, Any],
) -> tuple[dict, dict]:
    """``(evs, opp_mass)`` for every node — the values persisted at job
    completion as ``strategy-<id>.evs.npz``."""
    ranges = spot_ranges(spot)
    if ranges is None:
        return {}, {}
    blocked_ids = tuple(
        parse_cards(spot.get("board") or ()) + parse_cards(spot.get("dead_cards") or ())
    )
    pot = float(spot.get("pot", 20.0))
    board0 = tuple(parse_cards(spot.get("board") or ()))
    return evec.all_node_evs(index, board0, pot, ranges[0], ranges[1], blocked_ids)


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
    pot = float(spot.get("pot", 20.0))   # SpotConfig defaults
    stack = float(spot.get("stack", 100.0))
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
    node_evs: dict[tuple[str, ...], dict[str, dict[str, float]]] | None = None,
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
    ranges = spot_ranges(spot)
    if ranges is None:
        raise ValueError("spot carries no parseable ranges")
    w0, w1 = ranges[0], ranges[1]
    blocked_ids = tuple(
        parse_cards(spot.get("board") or ()) + parse_cards(spot.get("dead_cards") or ())
    )
    reach0, reach1 = reach_at(index, hist, w0, w1, blocked_ids)
    board_now = dealt_board(spot.get("board") or (), hist)
    pot = float(spot.get("pot", 20.0))
    if node_evs is not None and hist in node_evs:
        evs = node_evs[hist]
    else:
        # per (combo, action): a subtree walk whose terminals pair the combo
        # against every opponent combo still reaching
        opp_combos = len(reach1) if player == 0 else len(reach0)
        work = index.descendants(hist) * len(rows) * max(1, opp_combos)
        if work > max_work:
            raise ValueError(
                "node evaluation needs %d combo-node visits (limit %d); narrow the spot or raise HOLDEM_NODE_EVAL_MAX_WORK"
                % (work, max_work)
            )
        evs = evec.node_action_evs(index, hist, reach0, reach1, board_now, pot)

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
