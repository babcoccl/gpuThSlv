"""Phase 8 step 1: reach-weighted hand-class aggregation.

Runs in both venvs: pure stdlib + protocol only. Covers
``scripts.phase7.grid.to_grid`` (TS-parse node shape) and
``holdem_solver.api.strategy.hand_grid`` (mapped shape) fed the same data.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.phase7.grid import to_grid
from holdem_solver.protocol.cards import combo_str, parse_range


def _ranges(range0, range1):
    return {
        0: {combo_str(c): w for c, w in parse_range(range0).items()},
        1: {combo_str(c): w for c, w in parse_range(range1).items()},
    }


def _cells(node):
    return {c["hand_class"]: c for c in node["grid"]}


def _nodes_root(strategy):
    """One decision point: player 0 at the root."""
    return [{"player": 0, "history": [], "actions": ["check", "b5"],
             "strategy": strategy}]


def test_weighted_vs_unweighted_differ():
    """``AsKs`` at weight 0.9 vs ``AhKh`` at 0.1 pulls the class mean."""
    strat = {"AsKs": {"check": 0.8, "b5": 0.2}, "AhKh": {"check": 0.2, "b5": 0.8}}
    nodes = _nodes_root(strat)
    plain = to_grid(nodes)
    weighted = to_grid(nodes, ranges=_ranges("AsKs:0.9,AhKh:0.1", "QQ"),
                     blocked=set())
    u = _cells(plain[0])["AKs"]["frequencies"]["check"]
    w = _cells(weighted[0])["AKs"]["frequencies"]["check"]
    assert u == pytest.approx(0.5)
    assert w == pytest.approx(0.9 * 0.8 + 0.1 * 0.2)
    assert w != u


def test_blocked_combo_contributes_zero():
    """Board Ks removes AsKs from the AKs class mean."""
    strat = {c: {"check": 0.5, "b5": 0.5} for c in ("AsKs", "AdKd", "AcKc", "AhKh")}
    strat["AhKh"] = {"check": 1.0, "b5": 0.0}
    nodes = _nodes_root(strat)
    weighted = to_grid(nodes, ranges=_ranges("AKs", "QQ"), blocked={"Ks"})
    aks = _cells(weighted[0])["AKs"]
    assert aks["combos"] == 3  # AsKs is dead
    assert aks["frequencies"]["check"] == pytest.approx(2.0 / 3)


def test_own_reach_zero_prunes_deeper_node():
    """A combo that never checks at the root has zero weight at check,b5."""
    nodes = [
        {"player": 0, "history": [], "actions": ["check", "b5"],
         "strategy": {"AsKs": {"check": 0.0, "b5": 1.0},
                      "AhKh": {"check": 1.0, "b5": 0.0}}},
        {"player": 1, "history": ["check"], "actions": ["check", "b5"],
         "strategy": {"QsQc": {"check": 0.0, "b5": 1.0}}},
        {"player": 0, "history": ["check", "b5"], "actions": ["call", "fold"],
         "strategy": {"AsKs": {"call": 0.5, "fold": 0.5},
                      "AhKh": {"call": 0.1, "fold": 0.9}}},
    ]
    weighted = to_grid(nodes, ranges=_ranges("AsKs,AhKh", "QsQc"), blocked=set())
    by = {(n["player"], tuple(n["history"])): n for n in weighted}
    aks = _cells(by[(0, ("check", "b5"))])["AKs"]
    # only AhKh reaches check,b5 -> its frequencies are the class mean
    assert aks["combos"] == 1
    assert aks["frequencies"] == pytest.approx({"call": 0.1, "fold": 0.9})
    assert aks["reach"] == pytest.approx(1.0 * 1.0 * 1.0)  # w * own_reach * opp_mass


def test_dealt_river_card_blocks_combo():
    """History ``river:Qh`` zeroes any combo holding Qh."""
    nodes = [
        {"player": 0, "history": [], "actions": ["check"],
         "strategy": {"KdKc": {"check": 1.0}}},
        {"player": 1, "history": ["check"], "actions": ["check"],
         "strategy": {"QhQc": {"check": 1.0}}},
        {"player": 0, "history": ["check", "check", "river:Qh"],
         "actions": ["check"], "strategy": {"KdKc": {"check": 1.0}}},
    ]
    weighted = to_grid(nodes, ranges=_ranges("KdKc", "QhQc"),
                       blocked={"Ts", "9s", "8s", "Jh"})
    by = {(n["player"], tuple(n["history"])): n for n in weighted}
    kk = _cells(by[(0, ("check", "check", "river:Qh"))])["KK"]
    # opponent's only combo (QhQc) is dead on this river -> opp_mass = 0
    assert kk["combos"] == 0
    assert kk["frequencies"] == {}
    assert kk["reach"] == 0.0


def test_uniform_range_matches_unweighted_at_root_when_symmetric():
    """Known-answer shape: uniform symmetric ranges give the unweighted mean."""
    strat = {"AsKs": {"check": 0.8, "b5": 0.2}, "AhKh": {"check": 0.4, "b5": 0.6}}
    nodes = _nodes_root(strat)
    weighted = to_grid(nodes, ranges=_ranges("AsKs,AhKh", "QQ,JJ"), blocked=set())
    # both AKs combos block the same number of QQ/JJ combos -> equal weights
    w = _cells(weighted[0])["AKs"]["frequencies"]
    assert w == pytest.approx({"check": 0.6, "b5": 0.4})


def test_api_hand_grid_matches_to_grid():
    """Both implementations produce identical weighted frequencies."""
    hand_grid = pytest.importorskip(
        "holdem_solver.api.strategy", reason="api package needs the api venv"
    ).hand_grid

    strat = {"AsKs": {"check": 0.8, "b5": 0.2}, "AhKh": {"check": 0.2, "b5": 0.8}}
    nodes = _nodes_root(strat) + [
        {"player": 1, "history": ["check"], "actions": ["fold", "call"],
         "strategy": {"QsQc": {"fold": 0.3, "call": 0.7}}},
    ]
    ranges = _ranges("AsKs:0.9,AhKh:0.1", "QsQc")
    expected = to_grid(nodes, ranges=ranges, blocked=set())

    infosets = []
    for node in nodes:
        for combo, freqs in node["strategy"].items():
            infosets.append({
                "key": "p%d|%s|%s" % (node["player"], combo, ",".join(node["history"])),
                "player": node["player"], "hand": combo, "history": node["history"],
                "actions": [
                    {"name": a, "label": a, "frequency": freqs[a]}
                    for a in node["actions"]
                ],
            })
    mapped = {"iteration": 5, "num_infosets": len(infosets), "infosets": infosets}
    got = hand_grid(mapped, ranges=ranges, blocked=set())
    by_got = {(n["player"], tuple(n["history"])): n for n in got["nodes"]}
    for enode in expected:
        gnode = by_got[(enode["player"], tuple(enode["history"]))]
        for ecell, gcell in zip(enode["grid"], gnode["grid"]):
            assert ecell["hand_class"] == gcell["hand_class"]
            assert ecell["combos"] == gcell["combos"]
            assert ecell["reach"] == pytest.approx(gcell["reach"])
            assert ecell["frequencies"] == pytest.approx(gcell["frequencies"])


def test_spot_ranges_helpers():
    """``spot_ranges``/``spot_blocked`` parse a stored spot dict."""
    strategy = pytest.importorskip(
        "holdem_solver.api.strategy", reason="api package needs the api venv"
    )
    spot_blocked, spot_ranges = strategy.spot_blocked, strategy.spot_ranges

    spot = {"board": ["Ks", "7h"], "dead_cards": ["2d"],
            "range0": "AA:0.5", "range1": "KK"}
    ranges = spot_ranges(spot)
    assert ranges[0]["AsAd"] == 0.5
    assert ranges[1]["KsKd"] == 1.0
    assert spot_blocked(spot) == {"Ks", "7h", "2d"}
    assert spot_ranges({}) is None
