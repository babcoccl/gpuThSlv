"""Runs in ``.venv-engine``.

Phase 6: the custom raise-grammar tree builder in ``engine_worker.tree_builder``.

Gate tests: ``raise_cap=0`` reproduces the shipped ``build_hunl_river_game`` /
``build_hunl_turn_game`` exactly (node count, infoset count, solved value and
exploitability). Raise tests: a polarized check-raise toy whose equilibrium is
computed here by fictitious play on the normal form, so the engine's answer is
checked against an independent derivation.
"""

import itertools

import numpy as np
import pytest

from holdem_solver.engine_worker.solver import SolverEngine, _load_gpu_cfr

_load_gpu_cfr()  # puts the vendored GPU-CFR on sys.path for tree_builder

from holdem_solver.engine_worker.tree_builder import build_river_spec, build_turn_spec
from holdem_solver.protocol.cards import card_id, combo_str, combos_conflict, parse_range

_BOARD_IDS = {card_id(card) for card in ("Ks", "Qs", "Jd", "7h", "2c")}


def _unblocked(range_text):
    return [combo for combo in parse_range(range_text)
            if combo[0] not in _BOARD_IDS and combo[1] not in _BOARD_IDS]

BOARD = ("Ks", "Qs", "Jd", "7h", "2c")
RIVER5 = ("Ks", "Js", "Th", "7d", "2c")


def _combos(range_text, board):
    blocked = {card_id(card) for card in board}
    return [combo for combo in parse_range(range_text)
            if combo[0] not in blocked and combo[1] not in blocked]


def _solve_spec(spec, iterations, device="cpu", **options):
    engine = SolverEngine()
    engine.build(spec, device=device, cuda_graph=False, **options)
    return engine.solve(iterations=iterations), engine.get_strategy()


def _shipped_river(board, pot, stack, fractions, hands0, hands1):
    from holdem_solver.engine_worker.solver import _load_gpu_cfr

    _load_gpu_cfr()
    from gpu_cfr.games.hunl_river import build_hunl_river_game

    return build_hunl_river_game(board, pot=pot, stack=stack,
                                 bet_fractions=fractions, hands0=hands0,
                                 hands1=hands1, name="gate")


def _shipped_turn(board, pot, stack, fractions, hands0, hands1):
    from holdem_solver.engine_worker.solver import _load_gpu_cfr

    _load_gpu_cfr()
    from gpu_cfr.games.hunl_turn import build_hunl_turn_game

    return build_hunl_turn_game(board, pot=pot, stack=stack,
                                bet_fractions=fractions, hands0=hands0,
                                hands1=hands1, name="gate")


@pytest.mark.parametrize(
    "board,hands0,hands1,pot,stack,fractions",
    [
        (RIVER5, "AhQh, 4c3c, 4d3d, 4h3h", "9d9h", 10, 10, (1.0,)),
        (RIVER5, "KK, 87o", "ATs, JJ, KQo, 65o", 4, 1.5, (0.125,)),
        (RIVER5, "QQ+, AKs", "TT-JJ, AQ", 10, 30, (0.5, 1.0)),
    ],
    ids=["kat-polarized", "check-raise-toy", "multi-size"],
)
def test_cap0_river_matches_shipped(board, hands0, hands1, pot, stack, fractions):
    """Consistency gate: cap 0 reproduces the shipped river builder exactly."""
    h0 = _combos(hands0, board)
    h1 = _combos(hands1, board)
    shipped = _shipped_river(board, pot, stack, fractions, h0, h1)
    custom = build_river_spec(board, pot=pot, stack=stack, bet_sizes=fractions,
                              hands0=h0, hands1=h1, raise_cap=0, name="gate")
    assert len(custom.nodes) == len(shipped.nodes)
    assert len(custom.infosets) == len(shipped.infosets)
    v_shipped, _ = _solve_spec(shipped, 2000)
    v_custom, _ = _solve_spec(custom, 2000)
    assert v_custom.profile_value_p0 == pytest.approx(v_shipped.profile_value_p0, abs=1e-3)
    assert v_custom.exploitability == pytest.approx(v_shipped.exploitability, abs=1e-3)


@pytest.mark.parametrize(
    "board,hands0,hands1,pot,stack,fractions",
    [
        (("As", "Ks", "7s", "2s"), "Qs3h, 5c4c", "JsTs", 10, 40, (1.0,)),
        (("Ks", "Qs", "Jd", "7h"), "KK, 87o", "AT, JJ", 4, 2.0, (0.125,)),
    ],
    ids=["kat-turn-flush", "turn-narrow"],
)
def test_cap0_turn_matches_shipped(board, hands0, hands1, pot, stack, fractions):
    """Consistency gate: cap 0 reproduces the shipped turn builder exactly."""
    h0 = _combos(hands0, board)
    h1 = _combos(hands1, board)
    shipped = _shipped_turn(board, pot, stack, fractions, h0, h1)
    custom = build_turn_spec(board, pot=pot, stack=stack, bet_sizes=fractions,
                             hands0=h0, hands1=h1, raise_cap=0, name="gate")
    assert len(custom.nodes) == len(shipped.nodes)
    assert len(custom.infosets) == len(shipped.infosets)
    v_shipped, _ = _solve_spec(shipped, 2000)
    v_custom, _ = _solve_spec(custom, 2000)
    assert v_custom.profile_value_p0 == pytest.approx(v_shipped.profile_value_p0, abs=1e-3)
    assert v_custom.exploitability == pytest.approx(v_shipped.exploitability, abs=1e-3)


def _fictitious_play(deals, pot, bet, raise_to, iters=60000):
    """Equilibrium of the check-raise toy by fictitious play on its normal form.

    ``deals`` is ``[(strength_class0, strength_class1), ...]`` over the real
    compatible combos; classes are ranks where lower loses to higher (``N`` top
    down to ``A`` air). Player 0 can only check; player 1 bets ``bet`` or
    checks; facing the bet player 0 folds, calls, or raises to ``raise_to``;
    player 1 then folds or calls. Returns ``(value, p0_marginals,
    p1_first_marginals, p1_raise_response_marginals)`` plus an exploitability
    upper bound for the converged profile.
    """
    order = ("N", "S", "M", "X", "A")
    strength = {label: len(order) - idx for idx, label in enumerate(order)}
    labels0 = ("S", "X")  # KK trips / pair of sevens
    labels1 = ("N", "M", "A")  # broadway / trips+two-pair / air
    idx0 = {label: i for i, label in enumerate(labels0)}
    idx1 = {label: i for i, label in enumerate(labels1)}
    p0_plans = list(itertools.product("fcr", repeat=2))
    p1_plans = list(itertools.product("kb", repeat=3))
    p1_resp = list(itertools.product("fc", repeat=3))
    columns = list(itertools.product(p1_plans, p1_resp))

    def payoff(l0, l1, plan0, plan1, resp1):
        s0, s1 = strength[l0], strength[l1]

        def showdown(total):
            return total / 2 if s0 > s1 else (-total / 2 if s0 < s1 else 0.0)

        if plan1[idx1[l1]] == "k":
            return showdown(pot)
        act = plan0[idx0[l0]]
        if act == "f":
            return -pot / 2
        if act == "c":
            return showdown(pot + 2 * bet)
        if resp1[idx1[l1]] == "f":
            return pot / 2 + bet
        return showdown(pot + 2 * raise_to)

    matrix = np.zeros((len(p0_plans), len(columns)))
    for i, plan0 in enumerate(p0_plans):
        for j, (plan1, resp1) in enumerate(columns):
            matrix[i, j] = sum(
                payoff(l0, l1, plan0, plan1, resp1) for l0, l1 in deals
            ) / len(deals)
    run_x = np.zeros(len(p0_plans))
    run_y = np.zeros(len(columns))
    for t in range(1, iters + 1):
        y = run_y / t
        x = run_x / t
        run_x[np.argmax(matrix @ y)] += 1
        run_y[np.argmin(matrix.T @ x)] += 1
    x = run_x / iters
    y = run_y / iters
    value = float(x @ matrix @ y)
    exploit_bound = float((matrix @ y).max() - (matrix.T @ x).min())

    def marginals(plans, dist, classes):
        out = {}
        for hi, label in enumerate(classes):
            counts = {}
            for i, plan in enumerate(plans):
                counts[plan[hi]] = counts.get(plan[hi], 0.0) + dist[i]
            out[label] = counts
        return out

    m0 = marginals(p0_plans, x, labels0)
    m1_first = {label: {} for label in labels1}
    m1_resp = {label: {} for label in labels1}
    for j, (plan1, resp1) in enumerate(columns):
        for hi, label in enumerate(labels1):
            m1_first[label][plan1[hi]] = m1_first[label].get(plan1[hi], 0.0) + y[j]
            m1_resp[label][resp1[hi]] = m1_resp[label].get(resp1[hi], 0.0) + y[j]
    return value, exploit_bound, m0, m1_first, m1_resp


@pytest.mark.gpu
def test_river_check_raise_known_answer():
    """The solver finds the fictitious-play equilibrium of a check-raise toy.

    Board ``KsQsJd7h2c``: strength order AT (broadway) > KK > JJ (trips) >
    KQo (two pair) > 87o (pair of sevens) > 65o (air). Player 0 = ``JJ, 87o``
    checks; player 1 = ``ATs, KQo, 65o`` bets 1/8 pot; player 0 may raise to
    2x (``r1``) once per street. The equilibrium has player 0's trip jacks
    raising for value ~100% and the two-pair bettors calling raises ~86%
    while air folds. JJ shares no cards with KQo/ATs, so blocker effects do
    not flip the raise's sign the way they can for KK vs KQo.
    """
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"

    hands0 = _combos("JJ,87o", BOARD)
    hands1 = _combos("ATs,KQo,65o", BOARD)
    spec = build_river_spec(
        BOARD, pot=4.0, stack=1.5,
        bet_sizes={0: (), 1: (0.125,)},
        hands0=hands0, hands1=hands1,
        raise_cap=1, raise_sizes=(("x", 2.0),), name="kat_check_raise",
    )

    # Real compatible deals, labelled by strength class, for the reference.
    labels0 = {"JJ": "S", "87o": "X"}
    labels1 = {"ATs": "N", "KQo": "M", "65o": "A"}

    def combo_label(combo, ranges):
        name = combo_str(combo)
        for rng, label in ranges.items():
            if combo in parse_range(rng):
                return label
        raise AssertionError("unclassified combo %s" % name)

    deals = [
        (combo_label(c0, labels0), combo_label(c1, labels1))
        for c0 in hands0 for c1 in hands1
        if not combos_conflict(c0, c1)
    ]
    value, bound, m0, m1_first, m1_resp = _fictitious_play(deals, 4.0, 0.5, 1.0)
    assert bound < 0.02  # the reference is an equilibrium, not a guess
    assert m0["S"]["r"] > 0.9
    assert m1_resp["M"]["c"] > 0.5

    result, strategy = _solve_spec(spec, 60000, device=device)
    assert result.exploitability < 0.01
    assert result.profile_value_p0 == pytest.approx(value, abs=0.02)

    def prob(key, action):
        names = spec.infosets[key].actions
        return float(strategy[key][names.index(action)])

    # Every JJ combo raises for value; pair-of-sevens almost never raises.
    for combo in _unblocked("JJ"):
        key = "p0|%s|check,b0.5" % combo_str(combo)
        assert prob(key, "r1") > 0.9
    for combo in _unblocked("87o"):
        key = "p0|%s|check,b0.5" % combo_str(combo)
        assert prob(key, "call") > 0.6
        assert prob(key, "r1") < 0.15

    # Middling bettors call raises ~86%; the nuts always calls; air always folds.
    call_freq = np.mean([
        prob("p1|%s|check,b0.5,r1" % combo_str(combo), "call")
        for combo in _unblocked("KQo")
    ])
    assert call_freq == pytest.approx(m1_resp["M"]["c"], abs=0.08)
    for combo in _unblocked("65o"):
        assert prob("p1|%s|check,b0.5,r1" % combo_str(combo), "fold") > 0.9
        # Bluff-bet frequency near the reference ~0.12.
        assert prob("p1|%s|check" % combo_str(combo), "b0.5") == pytest.approx(
            m1_first["A"]["b"], abs=0.08)


def test_raise_grammar_semantics():
    """Raise names, the cap, and all-in capping on a tiny probe."""
    hands0 = _combos("KK", BOARD)
    hands1 = _combos("JJ", BOARD)
    spec = build_river_spec(
        BOARD, pot=4.0, stack=1.5,
        bet_sizes={0: (), 1: (0.125,)},
        hands0=hands0, hands1=hands1,
        raise_cap=2, raise_sizes=(("x", 2.0), ("p", 1.0)), name="probe",
    )
    kk = next(iter(spec.infosets[k] for k in spec.infosets
                 if k.startswith("p0|") and k.endswith("|check,b0.5")))
    # ("x",2.0) -> raise to 1.0; ("p",1.0) -> 0.5 + (4.5+0.5)=5.5 capped at stack 1.5.
    assert kk.actions == ("fold", "call", "r1", "r1.5")
    # cap=2: after r1 the raiser faces a re-raise option; verify a p1 raise exists
    facing_r1 = [k for k in spec.infosets if k.startswith("p1|") and k.endswith("|check,b0.5,r1")]
    assert facing_r1
    actions = spec.infosets[facing_r1[0]].actions
    assert actions[:2] == ("fold", "call")
    assert any(a.startswith("r") for a in actions[2:])  # re-raise allowed at cap 2

    spec1 = build_river_spec(
        BOARD, pot=4.0, stack=1.5,
        bet_sizes={0: (), 1: (0.125,)},
        hands0=hands0, hands1=hands1,
        raise_cap=1, raise_sizes=(("x", 2.0), ("p", 1.0)), name="probe",
    )
    actions1 = spec1.infosets[
        next(k for k in spec1.infosets if k.startswith("p1|") and k.endswith("|check,b0.5,r1"))
    ].actions
    assert actions1 == ("fold", "call")  # cap exhausted

    # All-in raise: stack 1.0 caps the raise target at 1.0.
    spec_ai = build_river_spec(
        BOARD, pot=4.0, stack=1.0,
        bet_sizes={0: (), 1: (0.125,)},
        hands0=hands0, hands1=hands1,
        raise_cap=1, raise_sizes=(("x", 2.0), ("p", 1.0)), name="probe",
    )
    ai = spec_ai.infosets[
        next(k for k in spec_ai.infosets if k.startswith("p0|") and k.endswith("|check,b0.5"))
    ]
    assert ai.actions == ("fold", "call", "r1")


def test_spot_config_raise_api():
    """``SpotConfig`` carries raise params through the wire form and gates."""
    from holdem_solver.engine_worker.spot_builder import SpotTooLargeError, build_spot, estimate_spot_nodes
    from holdem_solver.protocol.spot import SpotConfig

    wire = {
        "board": ["Ks", "Qs", "Jd", "7h", "2c"],
        "range0": "JJ,87o", "range1": "ATs,KQo,65o",
        "pot": 4.0, "stack": 1.5,
        "bet_sizes": {"1": [0.125], "0": []},
        "raise_cap": 1, "raise_sizes": [["x", 2.0]],
    }
    config = SpotConfig.from_dict(wire)
    assert SpotConfig.from_dict(config.to_dict()).to_dict() == config.to_dict()
    assert config.needs_custom_builder

    built = build_spot(config)
    assert built.info["raise_cap"] == 1 and built.info["custom_builder"]
    assert estimate_spot_nodes(config) == len(built.spec.nodes)

    too_big = SpotConfig.from_dict(dict(wire, max_nodes=len(built.spec.nodes) - 1))
    try:
        build_spot(too_big)
        raise AssertionError("max_nodes gate did not fire")
    except SpotTooLargeError:
        pass

    # Per-street bet sizes on a turn spot with raises on.
    turn = SpotConfig(
        board=("As", "Kd", "8c", "4h"), range0="TT,AK", range1="QQ,JJ",
        pot=20, stack=90, bet_sizes={"turn": [0.5], "river": [0.75]}, raise_cap=1,
    )
    built_turn = build_spot(turn)
    assert estimate_spot_nodes(turn) == len(built_turn.spec.nodes)

    # Validation rejects bad raise params and mixed/misplaced bet-size maps.
    for bad in (
        dict(raise_cap=-1),
        dict(raise_sizes=[["x", 2.0]]),  # sizes without a cap
        dict(bet_sizes={"flop": [0.5]}),
        dict(bet_sizes={"turn": [0.5], "river": [0.5]}),  # river board
        dict(bet_sizes={"0": [0.5], "turn": [0.5]}),
        dict(bet_sizes={"0": []}),  # both sides empty
        dict(bet_sizes={"0": [0.5], "1": [-0.25]}),
        dict(raise_sizes=[["q", 1.0]]),
    ):
        data = dict(board=["Ks", "Qs", "Jd", "7h", "2c"], range0="KK", range1="AA", pot=4, stack=2)
        data.update(bad)
        try:
            SpotConfig.from_dict(data)
            raise AssertionError("no ValueError for %r" % (bad,))
        except ValueError:
            pass
