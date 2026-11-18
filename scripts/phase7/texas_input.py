"""Runs in ``.venv-engine`` and ``.venv-api``.

Render a ``SpotConfig`` to a TexasSolver console input file.

Mapping conventions (verified against TexasSolver v0.2.0 source):
* range0 -> ``set_range_oop`` (player 1), range1 -> ``set_range_ip`` (player 0)
* pot fractions -> percents (``bet,50`` = half pot)
* our ``("p", f)`` raise -> ``raise,<100f>``  (raise-to = bet level + f*pot
  after calling; TS rounds pct*pot to whole chips before adding the call)
* our ``("x", m)`` raise cannot be expressed by TS -> ValueError
* ``all_in`` -> ``bet_size_map`` already appends ``stack/pot`` (the all-in
  amount), so no extra ``allin`` line is emitted; ``raise_cap=0`` -> an
  empty ``raise`` line
* ``emit_donk=True`` emits ``oop,<street>,donk,<sizes>`` equal to OOP's
  bet fractions (verified configurable in Phase 8.5; default False keeps
  Phase 7/8 inputs unchanged)
* isomorphism always off; allin_threshold fixed at 0.67
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from holdem_solver.protocol.spot import SpotConfig

TS_PLAYER = {0: "oop", 1: "ip"}  # our player -> TexasSolver name

_RANKS = "AKQJT98765432"


def _full_range() -> str:
    """TexasSolver rejects ``*``: expand it to the explicit 169-class list."""
    classes = []
    for i, hi in enumerate(_RANKS):
        classes.append(hi + hi)
        for lo in _RANKS[i + 1:]:
            classes.append(hi + lo + "s")
    for i, hi in enumerate(_RANKS):
        for lo in _RANKS[i + 1:]:
            classes.append(hi + lo + "o")
    return ",".join(classes)


def _range_text(r: str) -> str:
    return _full_range() if r.strip() == "*" else r


def _as_fractions(sizes: Union[str, Tuple[float, ...]]) -> Tuple[float, ...]:
    if isinstance(sizes, str):
        return sizes  # named set handled by caller via bet_size_map
    return tuple(float(s) for s in sizes)


def _pct(fraction: float) -> str:
    value = fraction * 100.0
    return "%g" % value


def spot_to_texas_input(
    spot: SpotConfig,
    dump_path: str,
    threads: int = 8,
    accuracy_pct: float = 0.5,
    max_iteration: int = 2000,
    print_interval: int = 100,
    allin_threshold: float = 2.0,
    emit_donk: bool = False,
    x_raise_pcts: Optional[Sequence[float]] = None,
) -> str:
    """Return the TexasSolver input-file text for ``spot``."""
    lines = []
    add = lines.append

    add("set_pot %g" % spot.pot)
    add("set_effective_stack %g" % spot.stack)
    add("set_board %s" % ",".join(spot.board))
    add("set_range_oop %s" % _range_text(spot.range0))
    add("set_range_ip %s" % _range_text(spot.range1))

    street = "river" if len(spot.board) == 5 else "turn"
    streets = [street] if street == "river" else ["turn", "river"]

    sizes = spot.bet_size_map()  # {player: fractions} or {street: fractions} or shared
    per_player = isinstance(sizes, Mapping) and set(sizes) <= {0, 1}
    per_street = isinstance(sizes, Mapping) and set(sizes) <= {"turn", "river"}

    for ts_street in streets:
        if street == "river" and ts_street != "river":
            continue
        for player in (0, 1):
            ts_player = TS_PLAYER[player]
            if per_player:
                fractions = tuple(float(s) for s in sizes[player])
            elif per_street:
                fractions = tuple(float(s) for s in sizes[ts_street])
            else:
                fractions = tuple(float(s) for s in sizes)
            if fractions:
                add("set_bet_sizes %s,%s,bet,%s" % (ts_player, ts_street, ",".join(_pct(f) for f in fractions)))
                if emit_donk and player == 0:
                    # OOP lead after a call-ended street: size it the same as
                    # the check-check bet so the TS tree matches ours.
                    add("set_bet_sizes oop,%s,donk,%s" % (ts_street, ",".join(_pct(f) for f in fractions)))
            raise_pcts = []
            x_iter = iter(x_raise_pcts or [])
            for kind, value in spot.raise_sizes:
                if kind == "p":
                    raise_pcts.append(_pct(value))
                elif kind == "x":
                    # TS raises are pct-of-pot increments over the bet level,
                    # so ('x', m) IS expressible per bet size: pct =
                    # 100*(m-1)*bet/(pot+2*bet). The caller must compute it.
                    try:
                        # already a percent (TS's raise,<pct> units)
                        raise_pcts.append("%g" % next(x_iter))
                    except StopIteration:
                        raise ValueError(
                            "TexasSolver cannot express ('x', %r) raises directly; "
                            "pass x_raise_pcts with the equivalent pot-fraction per "
                            "'x' entry" % (value,)
                        )
            if spot.raise_cap == 0:
                add("set_bet_sizes %s,%s,raise" % (ts_player, ts_street))
            elif raise_pcts:
                add("set_bet_sizes %s,%s,raise,%s" % (ts_player, ts_street, ",".join(raise_pcts)))
            else:
                add("set_bet_sizes %s,%s,raise" % (ts_player, ts_street))

    if spot.dead_cards:
        # TexasSolver v0.2.0 has no dead-cards command; our builder removes
        # them from dealt cards on river spots only.
        raise ValueError("SpotConfig.dead_cards cannot be expressed in TexasSolver")

    add("set_allin_threshold %g" % allin_threshold)
    add("build_tree")
    add("set_thread_num %d" % threads)
    add("set_accuracy %g" % accuracy_pct)
    add("set_max_iteration %d" % max_iteration)
    add("set_print_interval %d" % print_interval)
    add("set_use_isomorphism 0")
    add("start_solve")
    dump_rounds = 1 if len(spot.board) == 5 else 2
    add("set_dump_rounds %d" % dump_rounds)
    add("dump_result %s" % dump_path)
    return "\n".join(lines) + "\n"
