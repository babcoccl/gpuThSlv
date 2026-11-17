"""Phase 8 step 3: evaluate TexasSolver's dumped strategy inside our evaluator.

Maps TS's per-combo per-node strategy onto our compiled spec's flat
per-infoset-action layout (fuzzy node/action matching, same tolerances as
``compare.py``) and runs ``TwoPlayerZeroSumEvaluator.evaluate`` on it. Infosets
absent from the dump (or combos absent from a node's strategy) fall back to
the uniform distribution — matching the engine's average-strategy fallback.

Also computes the EV-weighted frequency gap per shared node: under the TS
profile, per-combo per-action EVs are measured by walking the dump tree, and
``|Σ_a σ_ours·EV_a − Σ_a σ_ts·EV_a|`` shows how much of the raw |Δf| is
EV-relevant. Python 3.9 safe.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from scripts.phase7.compare import _amounts_close, _drop_dead_rivers, action_key


def _fuzzy_find(ts_idx, player: int, history: List[str]):
    sig = tuple(action_key(h)[0] for h in history)
    for cand in ts_idx.get((player, sig), []):
        if len(cand["history"]) != len(history):
            continue
        ok = True
        for ha, hb in zip(history, cand["history"]):
            if ha.startswith("river:") or hb.startswith("river:"):
                if ha != hb:
                    ok = False
                    break
                continue
            ka, aa = action_key(ha)
            kb, ab = action_key(hb)
            if ka != kb or not _amounts_close(aa, ab):
                ok = False
                break
        if ok:
            return cand
    return None


def ts_flat_strategy(
    spec: Any,
    ts_nodes: List[Dict[str, Any]],
    board: Optional[List[str]] = None,
    key_order: Optional[List[str]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Flat strategy aligned with the engine's flat layout from a TS dump.

    The flat layout is the *compiled* game's infoset order — the order of
    ``solver.average_strategy()``'s keys — which differs from
    ``spec.infosets`` dict order. Pass ``key_order`` =
    ``list(solver.average_strategy())``.

    Returns ``(flat, info)``; ``info`` reports mapping coverage.
    """
    ts_nodes = _drop_dead_rivers(ts_nodes, board)
    ts_idx = {}
    for n in ts_nodes:
        sig = tuple(action_key(h)[0] for h in n["history"])
        ts_idx.setdefault((n["player"], sig), []).append(n)

    from scripts.phase7.ours import parse_infoset_key

    keys = list(key_order) if key_order is not None else list(spec.infosets.keys())
    sizes = [len(spec.infosets[k].actions) for k in keys]
    flat = np.zeros(sum(sizes), dtype=np.float64)
    mapped = skipped = 0
    combos_filled = combos_total = 0
    off = 0
    for key, nact in zip(keys, sizes):
        parsed = parse_infoset_key(key)
        names = [
            getattr(a, "name", str(a)) for a in spec.infosets[key].actions
        ]
        node = None
        if parsed is not None:
            node = _fuzzy_find(ts_idx, parsed["player"], parsed["history"])
        if node is None:
            flat[off : off + nact] = 1.0 / nact
            skipped += 1
        else:
            mapped += 1
            strat = node.get("strategy", {})
            combos_total += 1
            combo = parsed["hand"]
            # TS strategy keys are canonical combos; ours are hand strings.
            freqs = strat.get(combo)
            if freqs is None:
                # TS canonical naming may differ in card order
                from scripts.phase7.texas_dump import canonical_combo
                freqs = strat.get(canonical_combo(combo))
            if freqs is None:
                flat[off : off + nact] = 1.0 / nact
            else:
                combos_filled += 1
                vals = []
                for name in names:
                    kind, amt = action_key(name)
                    match = None
                    for tname, tf in freqs.items():
                        tk, tamt = action_key(tname)
                        if tk == kind and _amounts_close(amt, tamt):
                            match = tf
                            break
                    vals.append(0.0 if match is None else float(match))
                total = sum(vals)
                if total > 0:
                    flat[off : off + nact] = np.array(vals) / total
                else:
                    flat[off : off + nact] = 1.0 / nact
        off += nact
    return flat, {
        "infosets": len(keys), "mapped": mapped, "uniform_fallback": skipped,
        "combos_filled": combos_filled, "combos_total": combos_total,
    }


# --- per-node EV diff (TS-profile downstream EVs) ---------------------------


def node_action_evs(
    dump_path: str,
    pot: float,
    board: List[str],
    range0: str,
    range1: str,
    max_combos: int = 200,
) -> Dict[Tuple[int, Tuple[str, ...]], Dict[str, Any]]:
    """Per action node: per-combo per-action EV under the dumped profile.

    EV(c, a) = p0 payoff of the child subtree with reach_p = {c: 1}, divided
    by the opponent reach mass over non-conflicting combos (the conditional
    EV of holding ``c`` at the node). Only computed for narrow ranges.
    """
    import json
    from holdem_solver.protocol.cards import combo_str, parse_range
    from scripts.phase7.texas_dump import (
        _ACTION_RE, _PLAYER_MAP, _combo_tuple, _num, canonical_combo, parse_card,
        rank7,
    )

    with open(dump_path) as fh:
        root = json.load(fh)
    board_ids = tuple(parse_card(c) for c in board)
    w0 = {combo_str(c): w for c, w in parse_range(range0.split(",")).items()}
    w1 = {combo_str(c): w for c, w in parse_range(range1.split(",")).items()}

    def combo_tuple(text):
        return _combo_tuple(text)

    def valid_mass(reach0, reach1):
        mass = 0.0
        for h0, r0 in reach0.items():
            c0t = combo_tuple(h0)
            inner = 0.0
            for h1, r1 in reach1.items():
                c1t = combo_tuple(h1)
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
            cards0 = combo_tuple(h0)
            v0 = rank7(board_now + cards0)
            wins = ties = 0.0
            for h1, r1 in reach1.items():
                cards1 = combo_tuple(h1)
                if cards0[0] in cards1 or cards0[1] in cards1:
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

    def walk(node, chips, reach0, reach1, board_now):
        ntype = node.get("node_type")
        if ntype == "chance_node":
            if node.get("deal_number", 0) == 0:
                c0, c1 = chips
                return showdown(reach0, reach1, c0, c1, board_now) - valid_mass(
                    reach0, reach1
                ) * (c0 + pot / 2)
            deck_left = 52 - len(board_now) - 4
            possible = [
                c for c in node.get("dealcards", {}) if parse_card(c) not in board_now
            ]
            w = 1.0 / deck_left
            total = 0.0
            for card in possible:
                dealt = parse_card(card)
                r0 = {h: r for h, r in reach0.items() if dealt not in combo_tuple(h)}
                r1 = {h: r for h, r in reach1.items() if dealt not in combo_tuple(h)}
                total += w * walk(
                    node["dealcards"][card], chips, r0, r1, board_now + (dealt,)
                )
            return total

        ours = _PLAYER_MAP[node["player"]]
        strat = node.get("strategy") or {}
        strat_actions = strat.get("actions") or node.get("actions", [])
        strat_combos = strat.get("strategy", {})
        reach = reach0 if ours == 0 else reach1
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
                chips_next = (
                    (c0 + float(amount), c1) if ours == 0 else (c0, c1 + float(amount))
                )
            else:
                chips_next = chips
            child_reach = dict(reach)
            seen = set()
            for combo, freqs in strat_combos.items():
                ours_combo = canonical_combo(combo)
                seen.add(ours_combo)
                child_reach[ours_combo] = reach.get(ours_combo, 0.0) * float(freqs[idx])
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
                child.get("node_type") == "chance_node"
                and child.get("deal_number", 0) == 0
            ):
                total += showdown(next0, next1, chips_next[0], chips_next[1], board_now)
                total -= valid_mass(next0, next1) * (chips_next[0] + pot / 2)
                continue
            total += walk(child, chips_next, next0, next1, board_now)
        return total

    def canon_action(raw, ours, chips):
        """Raw dump action -> parse_dump canonical name (check/call/fold/
        b<amt>/r<level>)."""
        kind, amount = _ACTION_RE.match(raw).group(1), _ACTION_RE.match(raw).group(2)
        if kind == "BET":
            return "b" + _num(float(amount))
        if kind == "RAISE":
            return "r" + _num(chips[ours] + float(amount))
        return kind.lower()

    # first pass: collect (history, chips, reach0, reach1, node) at action nodes
    targets = {}  # type: Dict[Tuple[int, Tuple[str, ...]], Tuple[Any, Any, Any, Any]]

    def collect(node, history, chips, reach0, reach1, board_now):
        ntype = node.get("node_type")
        if ntype == "chance_node":
            if node.get("deal_number", 0) == 0:
                return
            for card in node.get("dealcards", {}):
                dealt = parse_card(card)
                if dealt in board_now:
                    continue
                r0 = {h: r for h, r in reach0.items() if dealt not in combo_tuple(h)}
                r1 = {h: r for h, r in reach1.items() if dealt not in combo_tuple(h)}
                collect(
                    node["dealcards"][card], history + ["river:" + card], chips,
                    r0, r1, board_now + (dealt,),
                )
            return
        ours = _PLAYER_MAP[node["player"]]
        key = (ours, tuple(history))
        if key not in targets:
            targets[key] = (node, chips, dict(reach0), dict(reach1))
        strat = node.get("strategy") or {}
        strat_actions = strat.get("actions") or node.get("actions", [])
        strat_combos = strat.get("strategy", {})
        reach = reach0 if ours == 0 else reach1
        childrens = node.get("childrens") or {}
        for raw in node.get("actions", []):
            match = _ACTION_RE.match(raw)
            kind, amount = match.group(1), match.group(2)
            idx = strat_actions.index(raw)
            c0, c1 = chips
            if kind == "CALL":
                level = max(c0, c1)
                chips_next = (level, c1) if ours == 0 else (c0, level)
            elif kind in ("BET", "RAISE"):
                chips_next = (
                    (c0 + float(amount), c1) if ours == 0 else (c0, c1 + float(amount))
                )
            else:
                chips_next = chips
            child_reach = dict(reach)
            seen = set()
            for combo, freqs in strat_combos.items():
                ours_combo = canonical_combo(combo)
                seen.add(ours_combo)
                child_reach[ours_combo] = reach.get(ours_combo, 0.0) * float(freqs[idx])
            for combo in reach:
                if combo not in seen:
                    child_reach[combo] = 0.0
            next0 = child_reach if ours == 0 else reach0
            next1 = reach1 if ours == 0 else child_reach
            child = childrens.get(raw)
            if child is None or (
                child.get("node_type") == "chance_node"
                and child.get("deal_number", 0) == 0
            ):
                continue
            collect(
                child, history + [canon_action(raw, ours, chips)], chips_next,
                next0, next1, board_now,
            )

    blocked = set(board_ids)
    init0 = {c: w for c, w in w0.items() if not (set(combo_tuple(c)) & blocked)}
    init1 = {c: w for c, w in w1.items() if not (set(combo_tuple(c)) & blocked)}
    collect(root, [], (0.0, 0.0), init0, init1, board_ids)

    out = {}
    for (ours, hist), (node, chips, reach0, reach1) in targets.items():
        strat = node.get("strategy") or {}
        strat_actions = strat.get("actions") or node.get("actions", [])
        strat_combos = strat.get("strategy", {})
        reach = reach0 if ours == 0 else reach1
        opp_reach = reach1 if ours == 0 else reach0
        if len(reach) > max_combos:
            continue
        # opponent reach mass over non-conflicting combos, per combo
        evs = {}  # type: Dict[str, Dict[str, float]]
        for combo in list(reach):
            if reach.get(combo, 0.0) <= 0.0:
                continue
            ct = combo_tuple(combo)
            opp_mass = sum(
                r for d, r in opp_reach.items()
                if ct[0] not in combo_tuple(d) and ct[1] not in combo_tuple(d)
            )
            if opp_mass <= 0.0:
                continue
            single0 = {combo: 1.0} if ours == 0 else reach0
            single1 = {combo: 1.0} if ours == 1 else reach1
            row = {}
            for raw in node.get("actions", []):
                match = _ACTION_RE.match(raw)
                kind, amount = match.group(1), match.group(2)
                c0, c1 = chips
                if kind == "CALL":
                    level = max(c0, c1)
                    chips_next = (level, c1) if ours == 0 else (c0, level)
                elif kind in ("BET", "RAISE"):
                    chips_next = (
                        (c0 + float(amount), c1)
                        if ours == 0
                        else (c0, c1 + float(amount))
                    )
                else:
                    chips_next = chips
                child = (node.get("childrens") or {}).get(raw)
                name = canon_action(raw, ours, chips)
                if kind == "FOLD":
                    v = (pot / 2 + c1) if ours == 1 else (-pot / 2 - c0)
                    row[name] = -v if ours == 1 else v  # to acting-player units
                    continue
                if child is None or (
                    child.get("node_type") == "chance_node"
                    and child.get("deal_number", 0) == 0
                ):
                    v = showdown(single0, single1, chips_next[0], chips_next[1],
                                 board_ids) - valid_mass(
                        single0, single1) * (chips_next[0] + pot / 2)
                    row[name] = (-v if ours == 1 else v) / opp_mass
                    continue
                v = walk(child, chips_next, single0, single1, board_ids)
                row[name] = (-v if ours == 1 else v) / opp_mass
            evs[combo] = row
        out[(ours, hist)] = {
            "evs": evs,
            "actions": [canon_action(a, ours, chips) for a in node.get("actions", [])],
        }
    return out
