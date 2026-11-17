"""Compare our solver output vs TexasSolver: trees, EV, frequencies.

Python 3.9 safe.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .grid import to_grid
from .texas_dump import parse_card

_AMT_RE = re.compile(r"^([bcr])([-\d.]+)$")


def _drop_dead_rivers(
    nodes: List[Dict[str, Any]], board: Optional[List[str]]
) -> List[Dict[str, Any]]:
    """Drop TS stub subtrees for river cards already on the board.

    TexasSolver dumps a dealcard subtree for all 52 cards; subtrees for
    board cards are unreachable and absent from our tree by construction.
    """
    if not board:
        return nodes
    dead = {parse_card(c) for c in board}
    out = []
    for n in nodes:
        if any(h.startswith("river:") and parse_card(h[6:]) in dead
               for h in n["history"]):
            continue
        out.append(n)
    return out


def action_key(name: str) -> Tuple[str, Optional[float]]:
    """``"b5"`` -> ``("b", 5.0)``; ``"check"`` -> ``("check", None)``."""
    match = _AMT_RE.match(name)
    if match:
        return match.group(1), float(match.group(2))
    return name, None


def _amounts_close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a == b
    return abs(a - b) <= 0.5 or abs(a - b) <= 0.01 * max(abs(a), abs(b))


def action_sets_equal(ours: List[str], ts: List[str]) -> Tuple[bool, List[str]]:
    """Compare action names as sets with amount tolerance. Returns (equal, notes)."""
    notes = []
    ok = {}
    for name in ours:
        kind, amt = action_key(name)
        ok[(kind, amt)] = name
    tk = {}
    for name in ts:
        kind, amt = action_key(name)
        tk[(kind, amt)] = name
    matched_ts = set()
    missing = []
    for (kind, amt), name in ok.items():
        found = None
        for (tkind, tamt), tname in tk.items():
            if tkind == kind and _amounts_close(amt, tamt):
                found = (tkind, tamt)
                break
        if found is None:
            missing.append(name)
        else:
            matched_ts.add(found)
    extra = [tname for key, tname in tk.items() if key not in matched_ts]
    for m in missing:
        notes.append("ours-only:%s" % m)
    for e in extra:
        notes.append("ts-only:%s" % e)
    return not missing and not extra, notes


def _hist_sig(history: List[str]) -> Tuple[str, ...]:
    """History signature: action kinds only (amounts compared fuzzily)."""
    return tuple(action_key(h)[0] for h in history)


def _hist_close(a: List[str], b: List[str]) -> bool:
    """Two histories match when kinds equal and amounts within tolerance."""
    if len(a) != len(b):
        return False
    for ha, hb in zip(a, b):
        ka, aa = action_key(ha)
        kb, ab = action_key(hb)
        if ka != kb or not _amounts_close(aa, ab):
            return False
    return True


def _fuzzy_index(nodes: List[Dict[str, Any]]):
    """{(player, kinds-sig): [nodes]} for fuzzy lookup by history."""
    idx = {}
    for n in nodes:
        sig = (n["player"], _hist_sig(n["history"]))
        idx.setdefault(sig, []).append(n)
    return idx


def _find_fuzzy(idx, node) -> Optional[Dict[str, Any]]:
    sig = (node["player"], _hist_sig(node["history"]))
    for cand in idx.get(sig, []):
        if _hist_close(node["history"], cand["history"]):
            return cand
    return None


def compare_trees(
    ours_nodes: List[Dict[str, Any]],
    ts_nodes: List[Dict[str, Any]],
    max_depth: int = 3,
    board: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compare action sets at every node with history length <= max_depth,
    matching nodes by fuzzy history (kind + amount within rounding tolerance)."""
    ts_nodes = _drop_dead_rivers(ts_nodes, board)
    ts_idx = _fuzzy_index(ts_nodes)
    ours_idx = _fuzzy_index(ours_nodes)
    # ours are per-hand infosets: collapse to one node per (player, history)
    collapsed = {}
    for n in ours_nodes:
        key = (n["player"], tuple(n["history"]))
        cur = collapsed.get(key)
        if cur is None:
            collapsed[key] = {"player": n["player"], "history": n["history"],
                              "actions": list(n["actions"])}
        else:
            cur["actions"] = sorted(set(cur["actions"]) | set(n["actions"]))
    ours_collapsed = list(collapsed.values())
    mismatches = []
    checked = 0
    ts_matched = set()
    for onode in ours_collapsed:
        if len(onode["history"]) > max_depth:
            continue
        tnode = _find_fuzzy(ts_idx, onode)
        if tnode is None:
            mismatches.append({"node": [onode["player"], onode["history"]],
                               "issue": "missing-in-ts", "ours": onode["actions"]})
            continue
        checked += 1
        ts_matched.add(id(tnode))
        equal, notes = action_sets_equal(onode["actions"], tnode["actions"])
        if not equal:
            mismatches.append(
                {"node": [onode["player"], onode["history"]], "issue": "action-set",
                 "ours": onode["actions"], "ts": tnode["actions"], "notes": notes}
            )
    for tnode in ts_nodes:
        if len(tnode["history"]) > max_depth or id(tnode) in ts_matched:
            continue
        if _find_fuzzy(ours_idx, tnode) is None:
            mismatches.append({"node": [tnode["player"], tnode["history"]],
                               "issue": "missing-in-ours", "ts": tnode["actions"]})
    return {"checked": checked, "mismatches": mismatches, "ok": not mismatches}


def _norm_action(name: str) -> str:
    kind, amt = action_key(name)
    return kind if amt is None else "%s%g" % (kind, amt)


def compare_strategies(
    ours_nodes: List[Dict[str, Any]],
    ts_nodes: List[Dict[str, Any]],
    pot: float,
    board: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Per-node hand-class frequency diffs, weighted by combos count."""
    ts_nodes = _drop_dead_rivers(ts_nodes, board)
    ours_grid = {(n["player"], tuple(n["history"])): n for n in to_grid(ours_nodes)}
    ts_grid_list = to_grid(ts_nodes)
    ts_grid = {(n["player"], tuple(n["history"])): n for n in ts_grid_list}
    ts_idx = _fuzzy_index(ts_grid_list)
    per_node = []
    total_w = 0.0
    total_err = 0.0
    global_max = 0.0
    ts_used = set()
    for key, onode in ours_grid.items():
        tnode = ts_grid.get(key)
        if tnode is None:
            tnode = _find_fuzzy(ts_idx, onode)
        if tnode is not None:
            ts_used.add(id(tnode))
        if tnode is None:
            per_node.append({"node": list(key), "issue": "missing-in-ts"})
            continue
        # align action names by kind+amount tolerance
        o_actions = [a["name"] for a in onode["actions"]]
        t_actions = [a["name"] for a in tnode["actions"]]
        pairs = []  # (ours_name, ts_name)
        for oa in o_actions:
            ok, oamt = action_key(oa)
            match_name = None
            for ta in t_actions:
                tk, tamt = action_key(ta)
                if tk == ok and _amounts_close(oamt, tamt):
                    match_name = ta
                    break
            if match_name is not None:
                pairs.append((oa, match_name))
        o_cells = {c["hand_class"]: c for c in onode["grid"]}
        t_cells = {c["hand_class"]: c for c in tnode["grid"]}
        wsum = 0.0
        err = 0.0
        mx = 0.0
        for cls, ocell in o_cells.items():
            tcell = t_cells.get(cls)
            if tcell is None or ocell["combos"] == 0:
                continue
            w = ocell["combos"]
            for oa, ta in pairs:
                d = abs(ocell["frequencies"].get(oa, 0.0) - tcell["frequencies"].get(ta, 0.0))
                err += w * d
                mx = max(mx, d)
            wsum += w * len(pairs)
        node_mean = err / wsum if wsum else 0.0
        per_node.append(
            {"node": list(key), "mean_abs_diff": node_mean, "max_abs_diff": mx,
             "combos": int(wsum / max(len(pairs), 1))}
        )
        total_w += wsum
        total_err += err
        global_max = max(global_max, mx)
    return {
        "per_node": per_node,
        "mean_abs_diff": (total_err / total_w) if total_w else 0.0,
        "max_abs_diff": global_max,
        "shared_nodes": sum(1 for n in per_node if "issue" not in n),
        "ours_only_nodes": len(ours_grid) - len(ts_used),
        "ts_only_nodes": len(ts_grid) - len(ts_used),
    }
