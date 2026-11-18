"""Runs in ``.venv-engine`` and ``.venv-api``.

Phase 8 weighted-range spots.

Weighted ranges use ``CLASS:fraction`` tokens — identical syntax on both
sides (our ``cards.parse_range`` and TexasSolver's ``set_range_*``).
Python 3.9 safe.
"""
from __future__ import annotations

from typing import List

from holdem_solver.protocol.spot import SpotConfig

DRY = ("Ks", "7h", "2d", "5c", "9s")
WET = ("Ts", "9s", "8s", "Jh", "2c")
PAIRED = ("Ks", "Kh", "7d", "7c", "2s")
BLOCKER = ("As", "Qd", "9h", "4c", "2s")

SPOTS: List[SpotConfig] = [
    # hand-checkable toy: weighted pairs on a dry board, cap 0
    SpotConfig(
        name="w-toy-check", board=DRY, range0="AA:0.5,KK", range1="QQ:0.25,JJ",
        pot=20.0, stack=100.0, bet_sizes=(0.5,), raise_cap=0,
    ),
    # dry river, AKo:0.5 / QQ:0.25-style weights on both players, cap 0 + 1
    SpotConfig(
        name="w-dry-cap0", board=DRY,
        range0="AKo:0.5,QQ:0.75,22", range1="AKo:0.25,JJ:0.5,TT",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0), raise_cap=0,
    ),
    SpotConfig(
        name="w-dry-cap1", board=DRY,
        range0="AKo:0.5,QQ:0.75,22", range1="AKo:0.25,JJ:0.5,TT",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0), raise_cap=1,
        raise_sizes=(("p", 0.6), ("p", 1.0)),
    ),
    # wet river, mixed weighted 3-bet-style ranges
    SpotConfig(
        name="w-wet-cap0", board=WET,
        range0="AA:0.75,KK:0.5,AKs:0.5,AKo:0.25,T9s",
        range1="QQ:0.5,JJ:0.75,AQs:0.5,AJo:0.25,98s:0.5",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0), raise_cap=0,
    ),
    # paired board
    SpotConfig(
        name="w-pair-cap0", board=PAIRED,
        range0="QQ:0.25,JJ,AKs:0.5", range1="AA:0.5,QQ,AJo:0.25",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0), raise_cap=0,
    ),
    # weighted range with blockers against the opponent's value hands:
    # OOP's AKo/KQo combos block IP's AA/KK/QQ value (A and Q also on board).
    SpotConfig(
        name="w-blocker-cap0", board=BLOCKER,
        range0="AKo:0.75,KQo:0.5,JTs:0.25", range1="AA:0.75,KK:0.5,QQ,99:0.25",
        pot=20.0, stack=100.0, bet_sizes=(0.5, 1.0), raise_cap=0,
    ),
    # turn spot with weights
    SpotConfig(
        name="w-turn-cap0", board=("Ts", "9s", "8s", "Jh"),
        range0="AA:0.5,KK", range1="QQ:0.25,JJ",
        pot=20.0, stack=60.0, bet_sizes={"turn": (0.75,), "river": (0.5,)},
        raise_cap=0,
    ),
    # full-width weighted river: broad 3-bet-style ranges on both sides
    SpotConfig(
        name="w-full-cap0", board=BLOCKER,
        range0="AA:0.5,KK:0.75,QQ,JJ,TT,99,AKs,AKo:0.25,AQs,AQo:0.5,AJs,ATs,"
               "KQs,KJs,QJs,JTs,T9s",
        range1="AA,KK:0.5,QQ:0.75,JJ,TT,AKs:0.5,AKo:0.25,AQs,AJs,KQs,QJs,JTs,"
               "T9s,98s",
        pot=20.0, stack=100.0, bet_sizes=(0.5,), raise_cap=0,
    ),
]
