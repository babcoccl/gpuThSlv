"""SpotConfig -> GameSpec with the real GPU-CFR builders, and known answers.

The known-answer spots are polarized-versus-bluff-catcher toy games with a
closed-form solution. Player 0 holds the nuts (fraction ``v`` of the range) or
air; player 1 holds one hand that beats air and loses to the nuts. With pot
``P``, bet ``B`` and enough air to bluff with:

* player 1 calls ``P / (P + B)`` so that air is indifferent to bluffing;
* air is worth 0 and the nuts ``P + B * P / (P + B)`` of the pot;
* the engine reports player 0's value net of the half pot each player sunk.
"""

import pytest

from holdem_solver.engine_worker.real import RealEngine
from holdem_solver.engine_worker.spot_builder import SpotTooLargeError, build_spot, estimate_spot_nodes
from holdem_solver.protocol import SpotConfig, combo_str

RIVER = ("Ks", "Js", "Th", "7d", "2c")
# Nut flush (Qs) for player 0, a lower flush for player 1, air that cannot
# improve past it: hand strengths are the same on every river card.
STATIC_TURN = ("As", "Ks", "7s", "2s")


def _torch():
    from holdem_solver.engine.solver import _load_gpu_cfr

    return _load_gpu_cfr().torch


def _solve(spot, iterations, device="cpu", **options):
    engine = RealEngine()
    try:
        engine.build(spot.to_dict(), device=device, **options)
        result = engine.solve(iterations=iterations)
        return result, engine.get_strategy(), engine.describe()
    finally:
        engine.close()


def test_hand_names_match_the_engine():
    _torch()
    from gpu_cfr.games import hunl_cards

    hands = hunl_cards.enumerate_hunl_hands([])
    assert len(hands) == 1326
    assert all(combo_str(tuple(hand)) == hunl_cards.hand_to_str(hand) for hand in hands)


@pytest.mark.parametrize(
    "spot",
    [
        SpotConfig(RIVER, "QQ+, AKs", "TT-JJ, AQ", pot=10, stack=30, bet_sizes="standard", all_in=True),
        SpotConfig(RIVER, "AA", "AKs, 9d9h", pot=10, stack=100, bet_sizes="wide"),
        SpotConfig(RIVER[:4], "AA, 4c3c", "QQ", pot=10, stack=25, bet_sizes="pot", all_in=True),
        SpotConfig(RIVER[:4], "AhQh", "9d9h, 8d8h", pot=10, stack=100, bet_sizes="standard"),
    ],
    ids=["river-allin", "river-wide", "turn-allin", "turn-standard"],
)
def test_built_tree_matches_the_config(spot):
    built = build_spot(spot)
    resolved = spot.resolve()
    spec = built.spec
    assert len(spec.nodes) == estimate_spot_nodes(spot)
    assert spec.name == spot.game_name
    assert spec.metadata["num_deals"] == resolved.num_deals == len(spec.nodes[spec.root].actions)
    assert tuple(spec.metadata["num_hands"]) == (len(resolved.hands0), len(resolved.hands1))
    deal_names = {action.name for action in spec.nodes[spec.root].actions}
    assert deal_names == {
        "%s:%s" % (combo_str(a), combo_str(b))
        for a, _ in resolved.hands0
        for b, _ in resolved.hands1
        if not set(a) & set(b)
    }
    key = "bet_amounts" if spot.street == "river" else "turn_bet_amounts"
    expected = sorted({min(fraction * spot.pot, spot.stack) for fraction in spot.bet_fractions()})
    assert list(spec.metadata[key]) == pytest.approx(expected)
    if spot.all_in:
        assert max(spec.metadata[key]) == spot.stack
    assert built.info["num_deals"] == resolved.num_deals and built.info["street"] == spot.street


def test_max_nodes_is_checked_before_building():
    spot = SpotConfig(RIVER, "*", "*", max_nodes=100000)
    with pytest.raises(SpotTooLargeError, match="over max_nodes=100000"):
        build_spot(spot)
    assert estimate_spot_nodes(spot) > 10**7


def test_river_polarized_known_answer():
    # v = 1/4, P = B = 10: value = v * (P + B*P/(P+B)) - P/2 = -1.25.
    spot = SpotConfig(RIVER, "AhQh, 4c3c, 4d3d, 4h3h", "9d9h", pot=10, stack=10, bet_sizes="pot")
    result, strategy, info = _solve(spot, 4000)
    assert info["spot"]["num_deals"] == 4 and info["num_nodes"] == 1 + 4 * 9
    assert result.profile_value_p0 == pytest.approx(-1.25, abs=0.01)
    assert strategy["p0|AhQh|"][1] == pytest.approx(1.0, abs=0.02)
    assert strategy["p1|9d9h|b10"][1] == pytest.approx(0.5, abs=0.02)
    # Bluffs : value bets = B / (P + B) = 1/2, spread over the three air hands.
    bluffs = sum(strategy["p0|%s|" % hand][1] for hand in ("4c3c", "4d3d", "4h3h"))
    assert bluffs == pytest.approx(0.5, abs=0.03)


def test_river_bet_size_choice_known_answer():
    # With 1/2-pot, pot and all-in (2x pot) available the nuts bet all-in:
    # value = v * (P + B*P/(P+B)) - P/2 with B = 20 is 1/4 * (10 + 20/3) - 5.
    spot = SpotConfig(RIVER, "AhQh, 4c3c, 4d3d, 4h3h", "9d9h", pot=10, stack=20, bet_sizes="standard", all_in=True)
    result, strategy, _ = _solve(spot, 8000)
    assert result.profile_value_p0 == pytest.approx(0.25 * (10 + 20 / 3) - 5, abs=0.02)
    assert strategy["p1|9d9h|b20"][1] == pytest.approx(1 / 3, abs=0.03)


def test_weighted_range_known_answer():
    # One air hand at weight 3 is the same game as three air hands at weight 1.
    spot = SpotConfig(RIVER, "AhQh, 4c3c:3", "9d9h", pot=10, stack=10, bet_sizes="pot")
    assert spot.resolve().weighted
    engine = RealEngine()
    try:
        engine.build(spot.to_dict(), device="cpu")
        assert engine.solve(iterations=4000).profile_value_p0 == pytest.approx(-1.25, abs=0.01)
        engine.reset()  # weights survive a reset
        assert engine.solve(iterations=4000).profile_value_p0 == pytest.approx(-1.25, abs=0.01)
    finally:
        engine.close()
    # Unweighted, the same two hands give v = 1/2: 1/2 * 15 - 5.
    uniform = SpotConfig(RIVER, "AhQh, 4c3c", "9d9h", pot=10, stack=10, bet_sizes="pot")
    assert _solve(uniform, 4000)[0].profile_value_p0 == pytest.approx(2.5, abs=0.01)


def test_blocker_known_answer():
    # Player 1 holds the Ah, so player 0 can never have AhQh: only air is
    # dealt, it loses every showdown and every bluff is called. Value = -P/2.
    spot = SpotConfig(RIVER, "AhQh, 4c3c", "AhJd", pot=10, stack=10, bet_sizes="pot")
    result, strategy, info = _solve(spot, 500)
    assert info["spot"]["num_deals"] == 1 and info["spot"]["num_blocked_pairs"] == 1
    assert result.profile_value_p0 == pytest.approx(-5.0, abs=1e-3)
    assert result.exploitability == pytest.approx(0.0, abs=1e-3)


def test_dead_card_known_answer():
    # The same removal through dead_cards: with the Ah dead, AhQh leaves the range.
    spot = SpotConfig(RIVER, "AhQh, 4c3c", "9d9h", pot=10, stack=10, bet_sizes="pot", dead_cards=("Ah",))
    result, _, info = _solve(spot, 500)
    assert info["spot"]["num_removed"] == [1, 0]
    assert result.profile_value_p0 == pytest.approx(-5.0, abs=1e-3)


@pytest.mark.gpu
def test_turn_two_street_known_answer_on_gpu():
    if not _torch().cuda.is_available():
        pytest.skip("CUDA is not available")
    # Two pot-sized bets (10, then 30 into 30). Player 1 calls 1/2 on each
    # street; the nuts win 5 + 1/2 * (45 - 10) = 22.5 of the pot, so
    # value = 1/4 * 22.5 - 5 = 0.625. On the turn player 0 bets 4 value : 5
    # bluffs, i.e. each of the three air hands bluffs 5/12.
    spot = SpotConfig(STATIC_TURN, "Qs3h, 5c4c, 5d4d, 5h4h", "JsTs", pot=10, stack=40, bet_sizes="pot")
    result, strategy, info = _solve(spot, 40000, device="cuda")
    assert info["num_nodes"] == estimate_spot_nodes(spot)
    assert info["estimated_vram_bytes"] > 0
    assert result.profile_value_p0 == pytest.approx(0.625, abs=0.03)
    assert abs(result.profile_value_p0 - 0.625) <= 2 * result.exploitability + 1e-3
    assert strategy["p1|JsTs|b10"][1] == pytest.approx(0.5, abs=0.02)
    assert strategy["p0|Qs3h|"][1] == pytest.approx(1.0, abs=0.02)
    assert strategy["p0|5c4c|"][1] == pytest.approx(5 / 12, abs=0.03)


@pytest.mark.gpu
def test_river_known_answer_on_gpu():
    if not _torch().cuda.is_available():
        pytest.skip("CUDA is not available")
    spot = SpotConfig(RIVER, "AhQh, 4c3c, 4d3d, 4h3h", "9d9h", pot=10, stack=10, bet_sizes="pot")
    result, strategy, _ = _solve(spot, 4000, device="cuda")
    assert result.profile_value_p0 == pytest.approx(-1.25, abs=0.01)
    assert strategy["p1|9d9h|b10"][1] == pytest.approx(0.5, abs=0.02)
