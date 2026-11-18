"""Runs in ``.venv-engine`` and ``.venv-api``.

Mapping-layer regression tests for Phase 8.5 additions.

Pure-python (no torch/gpu_cfr): collected by both venvs.
"""
from __future__ import annotations

import pytest

from holdem_solver.protocol.spot import SpotConfig
from scripts.phase7.texas_input import spot_to_texas_input


RIVER = SpotConfig(
    name="t", board=["Ks", "7h", "2d", "5c", "9s"], range0="AA,KK",
    range1="QQ,JJ", pot=20.0, stack=100.0, bet_sizes=(0.5,), raise_cap=0,
)
TURN = SpotConfig(
    name="t", board=["Ks", "7h", "2d", "5c"], range0="AA,KK",
    range1="QQ,JJ", pot=20.0, stack=60.0,
    bet_sizes={"turn": (0.75,), "river": (0.5,)}, raise_cap=0,
)


def test_donk_not_emitted_by_default():
    assert "donk" not in spot_to_texas_input(RIVER, "/tmp/d.json")


def test_donk_emitted_matches_oop_bet_sizes():
    text = spot_to_texas_input(RIVER, "/tmp/d.json", emit_donk=True)
    assert "set_bet_sizes oop,river,bet,50" in text
    assert "set_bet_sizes oop,river,donk,50" in text
    assert "ip,river,donk" not in text


def test_donk_emitted_on_turn_and_river_for_turn_spots():
    text = spot_to_texas_input(TURN, "/tmp/d.json", emit_donk=True)
    assert "set_bet_sizes oop,turn,donk,75" in text
    assert "set_bet_sizes oop,river,donk,50" in text


def test_x_raise_rejected_without_equivalent_pcts():
    spot = SpotConfig(
        name="t", board=["Ks", "7h", "2d", "5c", "9s"], range0="AA,KK",
        range1="QQ,JJ", pot=20.0, stack=100.0, bet_sizes=(0.5,),
        raise_cap=1, raise_sizes=(("x", 3.0),),
    )
    with pytest.raises(ValueError, match="x_raise_pcts"):
        spot_to_texas_input(spot, "/tmp/d.json")


def test_x_raise_emitted_with_equivalent_pcts():
    spot = SpotConfig(
        name="t", board=["Ks", "7h", "2d", "5c", "9s"], range0="AA,KK",
        range1="QQ,JJ", pot=20.0, stack=100.0, bet_sizes=(0.5,),
        raise_cap=1, raise_sizes=(("x", 3.0), ("p", 1.0)),
    )
    text = spot_to_texas_input(spot, "/tmp/d.json", x_raise_pcts=[50.0])
    assert "set_bet_sizes oop,river,raise,50,100" in text


def test_per_player_bet_sizes_emitted():
    spot = SpotConfig(
        name="t", board=["Ks", "7h", "2d", "5c", "9s"], range0="AA,KK",
        range1="QQ,JJ", pot=20.0, stack=100.0,
        bet_sizes={"0": (0.33,), "1": (0.66,)}, raise_cap=0,
    )
    text = spot_to_texas_input(spot, "/tmp/d.json")
    assert "set_bet_sizes oop,river,bet,33" in text
    assert "set_bet_sizes ip,river,bet,66" in text
