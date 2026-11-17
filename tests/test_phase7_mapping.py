"""Phase 7: SpotConfig <-> TexasSolver mapping round-trip tests.

Runs in both venvs: pure stdlib + protocol only.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from holdem_solver.protocol.spot import SpotConfig
from scripts.phase7.texas_input import spot_to_texas_input
from scripts.phase7.texas_dump import parse_dump, dump_ev, rank7, canonical_combo
from scripts.phase7.grid import to_grid, hand_class


RIVER_SPOT = SpotConfig(
    board=("Ks", "Qh", "Jd", "2c", "7h"),
    range0="AA,KK",
    range1="QQ,JJ",
    pot=10.0,
    stack=30.0,
    bet_sizes=(0.5,),
)

RAISE_SPOT = SpotConfig(
    board=("Ks", "Qh", "Jd", "2c", "7h"),
    range0="AA,KK",
    range1="QQ,JJ",
    pot=10.0,
    stack=30.0,
    bet_sizes=(0.5, 1.0),
    all_in=True,
    raise_cap=2,
    raise_sizes=(("p", 0.6), ("p", 1.0)),
)

TURN_SPOT = SpotConfig(
    board=("Ks", "Qh", "Jd", "2c"),
    range0="AA,KK",
    range1="QQ,JJ",
    pot=10.0,
    stack=30.0,
    bet_sizes={"turn": (0.5,), "river": (0.75,)},
    raise_cap=1,
    raise_sizes=(("p", 0.6),),
)


def _lines(spot):
    return spot_to_texas_input(spot, "/tmp/out.json", threads=8).splitlines()


def test_input_ranges_and_board():
    lines = _lines(RIVER_SPOT)
    assert "set_range_oop AA,KK" in lines
    assert "set_range_ip QQ,JJ" in lines
    assert "set_board Ks,Qh,Jd,2c,7h" in lines
    assert "set_pot 10" in lines
    assert "set_effective_stack 30" in lines


def test_input_bet_fraction_to_percent():
    lines = _lines(RIVER_SPOT)
    assert "set_bet_sizes oop,river,bet,50" in lines
    assert "set_bet_sizes ip,river,bet,50" in lines
    assert "set_bet_sizes oop,river,raise" in lines  # cap 0 -> disabled
    assert "set_bet_sizes ip,river,raise" in lines
    assert not any("donk" in line for line in lines)
    assert "set_use_isomorphism 0" in lines
    assert "set_dump_rounds 1" in lines


def test_input_raises_and_allin():
    lines = _lines(RAISE_SPOT)
    assert "set_bet_sizes oop,river,raise,60,100" in lines
    # all_in -> stack/pot = 3.0 appended to the bet sizes (no separate line)
    assert "set_bet_sizes oop,river,bet,50,100,300" in lines
    assert not any(",allin" in line for line in lines)


def test_input_turn_streets():
    lines = _lines(TURN_SPOT)
    assert "set_bet_sizes oop,turn,bet,50" in lines
    assert "set_bet_sizes oop,river,bet,75" in lines
    assert "set_bet_sizes oop,turn,raise,60" in lines
    assert "set_bet_sizes oop,river,raise,60" in lines
    assert "set_dump_rounds 2" in lines


def test_input_x_raise_rejected():
    spot = SpotConfig(
        board=("Ks", "Qh", "Jd", "2c", "7h"), range0="AA", range1="KK",
        raise_cap=1, raise_sizes=(("x", 2.0),),
    )
    with pytest.raises(ValueError):
        _lines(spot)


def _synthetic_dump(path):
    """check / bet5 / call-fold tree: root OOP; IP faces bet."""
    dump = {
        "node_type": "action_node",
        "player": 1,
        "actions": ["CHECK", "BET 5.000000"],
        "childrens": {
            "CHECK": {
                "node_type": "action_node",
                "player": 0,
                "actions": ["CHECK", "BET 5.000000"],
                "childrens": {
                    "CHECK": {"node_type": "chance_node", "deal_number": 0},
                    "BET 5.000000": {
                        "node_type": "action_node",
                        "player": 1,
                        "actions": ["CALL", "RAISE 15.000000", "FOLD"],
                        "childrens": {
                            "CALL": {"node_type": "chance_node", "deal_number": 0},
                            "RAISE 15.000000": {
                                "node_type": "action_node",
                                "player": 0,
                                "actions": ["CALL", "FOLD"],
                                "childrens": {
                                    "CALL": {"node_type": "chance_node", "deal_number": 0},
                                    "FOLD": {"node_type": "chance_node", "deal_number": 0},
                                },
                                "strategy": {
                                    "actions": ["CALL", "FOLD"],
                                    "strategy": {"QhQc": [0.25, 0.75]},
                                },
                            },
                            "FOLD": {"node_type": "chance_node", "deal_number": 0},
                        },
                        "strategy": {
                            "actions": ["CALL", "RAISE 15.000000", "FOLD"],
                            "strategy": {"AhAc": [0.5, 0.5, 0.0]},
                        },
                    },
                },
                "strategy": {
                    "actions": ["CHECK", "BET 5.000000"],
                    "strategy": {"QhQc": [0.6, 0.4]},
                },
            },
            "BET 5.000000": {
                "node_type": "action_node",
                "player": 0,
                "actions": ["CALL", "FOLD"],
                "childrens": {
                    "CALL": {"node_type": "chance_node", "deal_number": 0},
                    "FOLD": {"node_type": "chance_node", "deal_number": 0},
                },
                "strategy": {
                    "actions": ["CALL", "FOLD"],
                    "strategy": {"QhQc": [0.8, 0.2]},
                },
            },
        },
        "strategy": {
            "actions": ["CHECK", "BET 5.000000"],
            "strategy": {"AhAc": [1.0, 0.0]},
        },
    }
    with open(path, "w") as fh:
        json.dump(dump, fh)
    return str(path)


def _turn_dump(path):
    """Turn tree: check/check -> chance node with dealcards, incl. a board stub.

    Board Ts,9s,8s,Jh; OOP KdKc vs IP QdQc (QQ already has the 8-Q straight).
    River Qh gives KK the K-high straight (wins +5); river 2d loses (-5);
    river Ts is an unreachable stub for a board card and must be skipped.
    """
    def river_node(combo):
        return {
            "node_type": "action_node", "player": 1, "actions": ["CHECK"],
            "childrens": {
                "CHECK": {
                    "node_type": "action_node", "player": 0, "actions": ["CHECK"],
                    "childrens": {
                        "CHECK": {"node_type": "chance_node", "deal_number": 0}
                    },
                    "strategy": {"actions": ["CHECK"],
                                 "strategy": {"QdQc": [1.0]}},
                }
            },
            "strategy": {"actions": ["CHECK"], "strategy": {combo: [1.0]}},
        }

    dump = {
        "node_type": "action_node", "player": 1, "actions": ["CHECK"],
        "childrens": {
            "CHECK": {
                "node_type": "action_node", "player": 0, "actions": ["CHECK"],
                "childrens": {
                    "CHECK": {
                        "node_type": "chance_node", "deal_number": 52,
                        "dealcards": {
                            "Qh": river_node("KdKc"),
                            "2d": river_node("KdKc"),
                            "Ts": river_node("KdKc"),  # board-card stub
                        },
                    }
                },
                "strategy": {"actions": ["CHECK"],
                             "strategy": {"QdQc": [1.0]}},
            }
        },
        "strategy": {"actions": ["CHECK"], "strategy": {"KdKc": [1.0]}},
    }
    with open(path, "w") as fh:
        json.dump(dump, fh)
    return str(path)


def test_dump_ev_turn_chance_node(tmp_path):
    """Dealt card extends the board; each real river weighs 1/44; stubs skip."""
    path = _turn_dump(tmp_path / "turn.json")
    board = ["Ts", "9s", "8s", "Jh"]
    ev = dump_ev(path, pot=10.0, board=board, range0="KdKc", range1="QdQc")
    # (+5 on river Qh - 5 on river 2d) / 44; the Ts stub contributes nothing.
    assert ev == pytest.approx(0.0, abs=1e-9)


def test_parse_dump_turn_histories(tmp_path):
    path = _turn_dump(tmp_path / "turn.json")
    nodes = parse_dump(path)
    hists = {tuple(n["history"]) for n in nodes}
    assert ("check", "check", "river:Qh") in hists
    assert ("check", "check", "river:Ts") in hists  # stub still parsed


def test_parse_dump_round_trip(tmp_path):
    path = _synthetic_dump(tmp_path / "dump.json")
    nodes = parse_dump(path)
    by_hist = {(n["player"], tuple(n["history"])): n for n in nodes}
    root = by_hist[(0, ())]
    assert set(root["actions"]) == {"check", "b5"}
    assert root["strategy"]["AcAh"] == {"check": 1.0, "b5": 0.0}
    ip_node = by_hist[(1, ("check",))]
    assert set(ip_node["actions"]) == {"check", "b5"}
    face = by_hist[(0, ("check", "b5"))]
    assert set(face["actions"]) == {"call", "r15", "fold"}
    face_ip = by_hist[(1, ("check", "b5", "r15"))]
    assert set(face_ip["actions"]) == {"call", "fold"}
    # history round-trips to infoset-key form
    assert face["history"] == ["check", "b5"]


def test_dump_ev_checkdown(tmp_path):
    # OOP always checks, IP always checks -> showdown on every deal.
    path = _synthetic_dump(tmp_path / "dump.json")
    # force all-check strategies: rewrite strategy freqs
    dump = json.load(open(path))
    dump["strategy"]["strategy"]["AhAc"] = [1.0, 0.0]
    dump["childrens"]["CHECK"]["strategy"]["strategy"]["QhQc"] = [1.0, 0.0]
    json.dump(dump, open(path, "w"))
    board = ["Ks", "Jh", "Td", "2c", "7h"]
    ev = dump_ev(path, pot=10.0, board=board, range0="AhAc", range1="QhQc")
    # AA always beats QQ at showdown: p0 wins pot 10 -> net +5.
    assert ev == pytest.approx(5.0, abs=1e-9)


def test_dump_ev_fold(tmp_path):
    path = _synthetic_dump(tmp_path / "dump.json")
    dump = json.load(open(path))
    # OOP always bets 5; IP always folds -> p0 nets pot/2 = +5.
    dump["strategy"]["strategy"]["AhAc"] = [0.0, 1.0]
    dump["childrens"]["BET 5.000000"]["strategy"]["strategy"]["QhQc"] = [0.0, 1.0]
    json.dump(dump, open(path, "w"))
    board = ["Ks", "Jh", "Td", "2c", "7h"]
    ev = dump_ev(path, pot=10.0, board=board, range0="AhAc", range1="QhQc")
    assert ev == pytest.approx(5.0, abs=1e-9)


def test_dump_ev_call_down(tmp_path):
    path = _synthetic_dump(tmp_path / "dump.json")
    dump = json.load(open(path))
    dump["strategy"]["strategy"]["AhAc"] = [0.0, 1.0]
    dump["childrens"]["BET 5.000000"]["strategy"]["strategy"]["QhQc"] = [1.0, 0.0]
    json.dump(dump, open(path, "w"))
    board = ["Ks", "Jh", "Td", "2c", "7h"]
    ev = dump_ev(path, pot=10.0, board=board, range0="AhAc", range1="QhQc")
    # showdown: p0 wins pot+bets=20 -> net 20 - 5 - 5 = +10.
    assert ev == pytest.approx(10.0, abs=1e-9)


def test_rank7_known_hands():
    from scripts.phase7.texas_dump import parse_card
    ids = [parse_card(c) for c in ("As", "Ks", "Qs", "Js", "Ts", "2h", "3d")]
    royal = rank7(tuple(ids[:5] + ids[5:7]))
    pair = rank7(tuple([parse_card(c) for c in ("As", "Ac", "Kd", "Qh", "7s", "5d", "3c")]))
    assert royal > pair
    wheel = rank7(tuple(parse_card(c) for c in ("As", "2d", "3c", "4h", "5s", "9d", "Tc")))
    pair2 = rank7(tuple(parse_card(c) for c in ("As", "Ac", "Kd", "Qh", "7s", "5d", "2c")))
    assert wheel > pair2


def test_canonical_combo():
    assert canonical_combo("AcAh") in {"AsAh", "AdAh", "AcAh", "AcAs"}
    # canonical = our combo_str ordering (low card id first)
    from holdem_solver.protocol.cards import combo_str, parse_combo
    assert canonical_combo("AcAh") == combo_str(parse_combo("AcAh"))


def test_grid_shape(tmp_path):
    path = _synthetic_dump(tmp_path / "dump.json")
    grid = to_grid(parse_dump(path))
    node = [n for n in grid if n["player"] == 0 and n["history"] == []][0]
    assert len(node["grid"]) == 169
    aa = [c for c in node["grid"] if c["hand_class"] == "AA"][0]
    assert aa["combos"] == 1
    assert aa["frequencies"]["check"] == pytest.approx(1.0)
