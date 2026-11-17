"""Build + solve a SpotConfig on our engine; emit nodes in TS-parse shape.

Runs under .venv-engine (Python 3.9). Returns the same node format as
``texas_dump.parse_dump`` plus EV/exploitability/timing. Python 3.9 safe.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Tuple

from holdem_solver.protocol.spot import SpotConfig

_KEY_RE = re.compile(r"^p([01])\|([^|]+)\|(.*)$")
_CARD_RE = re.compile(r"^[2-9TJQKA][cdhs]$")


def parse_infoset_key(key: str):
    """``{player, hand, history}`` — mirrors api.strategy.parse_infoset_key."""
    match = _KEY_RE.match(key)
    if match is None:
        return None
    history = []
    for section in match.group(3).split("|"):
        if _CARD_RE.match(section):
            history.append("river:" + section)
        elif section:
            history.extend(a for a in section.split(",") if a)
    return {"player": int(match.group(1)), "hand": match.group(2), "history": history}


def tree_nodes(spot: SpotConfig, device: str = "cuda") -> Tuple[List[Dict[str, Any]], int]:
    """Build the spot and return nodes (actions/history; strategy empty) + node count."""
    from holdem_solver.engine_worker.spot_builder import build_spot

    built = build_spot(spot)
    nodes = []
    for key, infoset in built.spec.infosets.items():
        parsed = parse_infoset_key(key)
        if parsed is None:
            continue
        nodes.append(
            {
                "player": parsed["player"],
                "history": parsed["history"],
                "actions": [getattr(a, "name", str(a)) for a in infoset.actions],
                "strategy": {},
            }
        )
    return nodes, len(built.spec.nodes)


def solve_ours(
    spot: SpotConfig,
    dtype: str = "float64",
    accuracy_pct: float = 0.5,
    device: str = "cuda",
) -> Dict[str, Any]:
    """Returns {nodes, ev_p0, exploitability_pct, build_s, solve_s, iterations, nodes_total}."""
    from holdem_solver.engine_worker.spot_builder import build_spot
    from holdem_solver.engine_worker.solver import SolverEngine

    t0 = time.perf_counter()
    built = build_spot(spot)
    engine = SolverEngine(built.spec, dtype=dtype, device=device)
    build_s = time.perf_counter() - t0

    target_chips = (accuracy_pct / 100.0) * 2.0 * spot.pot  # 2*pot normalizer -> chips
    t0 = time.perf_counter()
    result = engine.solve(target_exploitability=target_chips)
    solve_s = result.elapsed_seconds

    strategy = engine.get_strategy()
    names = engine.strategy_action_names()
    nodes = []  # type: List[Dict[str, Any]]
    for key, row in strategy.items():
        parsed = parse_infoset_key(key)
        if parsed is None:
            continue
        acts = names[key]
        nodes.append(
            {
                "player": parsed["player"],
                "history": parsed["history"],
                "actions": list(acts),
                "strategy": {
                    parsed["hand"]: {name: float(v) for name, v in zip(acts, row.tolist())}
                },
            }
        )
    expl_pct = result.exploitability / (2.0 * spot.pot) * 100.0
    return {
        "nodes": nodes,
        "ev_p0": float(result.profile_value_p0),
        "exploitability_pct": expl_pct,
        "exploitability_chips": float(result.exploitability),
        "build_s": build_s,
        "solve_s": solve_s,
        "iterations": result.iterations_run,
        "num_tree_nodes": len(built.spec.nodes),
        "target_reached": result.target_reached,
    }
