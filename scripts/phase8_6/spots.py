"""Phase 8.6 spots: structurally equal trees vs TexasSolver's raise limit.

TexasSolver's ``raise_limit`` is a hard-coded 4 (``CommandLineTool.h`` /
``qsolverjob.h``) counted per street by a shared ``raise_times`` counter:
the opening bet sets it to 1 and every raise (either player's) adds 1, so a
street allows at most **3 raises**. Our ``raise_cap`` counts raise actions
only, per street. Equal trees therefore occur at ``raise_cap`` 0, at
``raise_cap`` 3, or at lower caps when the extra raises TS allows cannot
legally occur (stack exhausted / first raise already all-in).

Python 3.9 safe.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from holdem_solver.protocol.spot import SpotConfig

DRY = ("Ks", "7h", "2d", "5c", "9s")
TURN_A = ("Ks", "7h", "2d", "5c")

# (spot, emit_donk, x_raise_pcts)
Entry = Tuple[SpotConfig, bool, Optional[List[float]]]

SPOTS: List[Entry] = [
    # turn-narrow-a re-run at raise_cap=3: TS allows bet + 3 raises per
    # street, so cap 3 makes the trees structurally equal and the Phase-7
    # D2-ext re-raise line (b5 -> r17 -> r30) now exists on both sides.
    (SpotConfig(
        name="turn-narrow-a-cap3", board=TURN_A,
        range0="AA,KK", range1="QQ,JJ", pot=10.0, stack=30.0,
        bet_sizes={"turn": (0.5,), "river": (0.5, 1.0)},
        raise_cap=3, raise_sizes=(("p", 0.6),),
    ), True, None),
    # turn-narrow-a with the raise replaced by an all-in: at cap 1 the
    # re-raise cannot exist on either side (the raiser is all-in), so the
    # trees are structurally equal at the spot's original cap. Emitted to TS
    # as raise,125 (= ("x",6.0) at the 0.5-pot turn bet); on the river the
    # same pct snaps to all-in on every line, matching ours.
    (SpotConfig(
        name="turn-narrow-a-allin", board=TURN_A,
        range0="AA,KK", range1="QQ,JJ", pot=10.0, stack=30.0,
        bet_sizes={"turn": (0.5,), "river": (0.5, 1.0)},
        raise_cap=1, raise_sizes=(("x", 6.0),),
    ), True, [125.0]),
    # cap 2 equal tree: the second raise is already all-in (stack 70:
    # b10 -> r30 -> r70), so TS's third raise cannot occur and our cap of
    # two raise actions is not a structural difference.
    (SpotConfig(
        name="river-cap2-eq", board=DRY,
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=70.0,
        bet_sizes=(0.5,), raise_cap=2, raise_sizes=(("p", 0.5),),
    ), False, None),
    # cap 3 equal tree: matches TS's raise_limit=4 (bet counts as one) with
    # room to spare — the deepest line is b10 -> r34 -> r86.8 -> r100(all-in).
    (SpotConfig(
        name="river-cap3-eq", board=DRY,
        range0="AA,KK", range1="QQ,JJ", pot=20.0, stack=100.0,
        bet_sizes=(0.5, 1.0), raise_cap=3,
        raise_sizes=(("p", 0.6), ("p", 1.0)),
    ), False, None),
]
