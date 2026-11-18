"""Runs in ``.venv-engine`` and ``.venv-api``.

The Phase 7 comparison suite: 15 spots.

Known-answer toys (2), river boards at cap 0/1/2 (9), narrow turns (2),
short-stack (1), full-range river (1). Only ('p', f) raise sizes are used so
the mapping is exact modulo TS's integer rounding. Narrow ranges keep
TexasSolver runtimes short; pot/stack chosen to avoid all-in snapping.
"""
from __future__ import annotations

from typing import List

from holdem_solver.protocol.spot import SpotConfig

TOY_RANGES = ("AA,KK", "QQ,JJ")


def _river(name, board, cap, bet_sizes=(0.5, 1.0), stack=100.0, pot=20.0,
           range0=TOY_RANGES[0], range1=TOY_RANGES[1], all_in=False):
    sizes = (("p", 0.6), ("p", 1.0)) if cap > 0 else ()
    return SpotConfig(
        name=name, board=board, range0=range0, range1=range1, pot=pot,
        stack=stack, bet_sizes=bet_sizes, all_in=all_in,
        raise_cap=cap, raise_sizes=sizes,
    )


DRY = ("Ks", "7h", "2d", "5c", "9s")     # no flush, unpaired, disconnected
WET = ("Ts", "9s", "8s", "Jh", "2c")     # monotone + straight-heavy
PAIRED = ("Ks", "Kh", "7d", "7c", "2s")  # paired

SPOTS: List[SpotConfig] = [
    # known-answer toys
    _river("toy-check", DRY, 0, bet_sizes=(0.5,)),
    _river("toy-bet", DRY, 0, bet_sizes=(0.5, 1.0), all_in=True, stack=30.0, pot=10.0),
    # dry board, caps 0/1/2
    _river("river-dry-cap0", DRY, 0),
    _river("river-dry-cap1", DRY, 1),
    _river("river-dry-cap2", DRY, 2),
    # wet board
    _river("river-wet-cap0", WET, 0),
    _river("river-wet-cap1", WET, 1),
    _river("river-wet-cap2", WET, 2),
    # paired board
    _river("river-pair-cap0", PAIRED, 0),
    _river("river-pair-cap1", PAIRED, 1),
    _river("river-pair-cap2", PAIRED, 2),
    # narrow turn spots
    SpotConfig(
        name="turn-narrow-a", board=("Ks", "7h", "2d", "5c"),
        range0="AA,KK", range1="QQ,JJ", pot=10.0, stack=30.0,
        bet_sizes={"turn": (0.5,), "river": (0.5, 1.0)},
        raise_cap=1, raise_sizes=(("p", 0.6),),
    ),
    SpotConfig(
        name="turn-narrow-b", board=("Ts", "9s", "8s", "Jh"),
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=60.0,
        bet_sizes={"turn": (0.75,), "river": (0.5,)},
        raise_cap=0,
    ),
    # short stack: stack/pot = 1.5 -> single bet is most of stack
    SpotConfig(
        name="short-stack", board=("As", "Qd", "9h", "4c", "2s"),
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=30.0,
        bet_sizes=(0.5, 1.0), all_in=True, raise_cap=1,
        raise_sizes=(("p", 1.0),),
    ),
    # full-range river: '*' = all 1326 combos; minimal tree (one bet size,
    # no raises) to keep node count feasible on our side.
    SpotConfig(
        name="river-full", board=("As", "Qd", "9h", "4c", "2s"),
        range0="*", range1="*",
        pot=20.0, stack=100.0, bet_sizes=(0.5,),
        raise_cap=0, max_nodes=None,
    ),
]
