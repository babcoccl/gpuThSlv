"""Runs in ``.venv-engine`` and ``.venv-api``.

Phase 8.5 spots: donk turn re-runs, per-player/per-street sizes, raises.

Python 3.9 safe.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from holdem_solver.protocol.spot import SpotConfig
from scripts.phase7.spots import SPOTS as P7_SPOTS
from scripts.phase8.spots import SPOTS as P8_SPOTS

DRY = ("Ks", "7h", "2d", "5c", "9s")
WET = ("Ts", "9s", "8s", "Jh", "2c")

# (spot, emit_donk, x_raise_pcts)
Entry = Tuple[SpotConfig, bool, Optional[List[float]]]


def _find(name: str) -> SpotConfig:
    for s in list(P7_SPOTS) + list(P8_SPOTS):
        if s.game_name == name:
            return s
    raise KeyError(name)


# Step 6: the turn spots re-run with OOP donk sizes matching our OOP river
# leads (turn-narrow-b river sizes (0.5,), turn-narrow-a river (0.5, 1.0),
# w-turn-cap0 river (0.5,)).
TURN_DONK: List[Entry] = [
    (_find("turn-narrow-a"), True, None),
    (_find("turn-narrow-b"), True, None),
    (_find("w-turn-cap0"), True, None),
]

# Step 7: per-player and per-street bet sizes.
SIZES: List[Entry] = [
    (SpotConfig(
        name="pp-river", board=DRY, range0="AA,KK", range1="QQ,JJ",
        pot=20.0, stack=100.0, bet_sizes={"0": (0.33,), "1": (0.66,)},
        raise_cap=0,
    ), False, None),
    (SpotConfig(
        name="pp-river-3sizes", board=WET, range0="AA,KK", range1="QQ,JJ",
        pot=20.0, stack=100.0,
        bet_sizes={"0": (0.33, 0.5, 1.0), "1": (0.5, 0.75, 1.5)},
        raise_cap=0,
    ), False, None),
    (SpotConfig(
        name="pp-river-allin", board=DRY, range0="AA,KK", range1="QQ,JJ",
        pot=20.0, stack=100.0, bet_sizes={"0": (0.33,), "1": (0.66,)},
        all_in=True, raise_cap=0,
    ), False, None),
    (SpotConfig(
        name="ps-turn-diff", board=("Ks", "7h", "2d", "5c"),
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=60.0,
        bet_sizes={"turn": (0.25,), "river": (1.25,)},
        raise_cap=0,
    ), True, None),
    (SpotConfig(
        name="pp-turn", board=("Ts", "9s", "8s", "Jh"),
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=60.0,
        bet_sizes={"0": (0.5,), "1": (1.0,)},
        raise_cap=0,
    ), True, None),
]

# Step 8: raise combinations.
RAISES: List[Entry] = [
    (SpotConfig(
        name="w-raise-cap2-dry", board=DRY,
        range0="AKo:0.5,QQ:0.75,22", range1="AKo:0.25,JJ:0.5,TT",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0),
        raise_cap=2, raise_sizes=(("p", 0.5), ("p", 1.0)),
    ), False, None),
    (SpotConfig(
        name="w-raise-cap2-wet", board=WET,
        range0="AA:0.75,KK:0.5,T9s", range1="QQ:0.5,JJ:0.75,98s:0.5",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0),
        raise_cap=2, raise_sizes=(("p", 0.5), ("p", 1.0)),
    ), False, None),
    # short-stack SPR=1.5 river, cap 1, all-in (same shape as Phase 7
    # short-stack; re-run here per spec).
    (SpotConfig(
        name="ss-raise-cap1", board=("As", "Qd", "9h", "4c", "2s"),
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=30.0,
        bet_sizes=(0.5, 1.0), all_in=True, raise_cap=1,
        raise_sizes=(("p", 1.0),),
    ), False, None),
    # Mixed raise kinds: ("x",3) raises to 3x the bet faced. At bet 0.5x20=10
    # the pot after calling is 20+2*10=40, so TS raise pct for a raise-to of
    # 30 is (30-10)/40 = 50% -> emit raise,50 alongside raise,100 for
    # ("p",1.0).
    (SpotConfig(
        name="xraise-mixed", board=DRY, range0="AA,KK", range1="QQ,JJ",
        pot=20.0, stack=100.0, bet_sizes=(0.5,),
        raise_cap=1, raise_sizes=(("x", 3.0), ("p", 1.0)),
    ), False, [50.0]),
]

SPOTS: List[Entry] = TURN_DONK + SIZES + RAISES
