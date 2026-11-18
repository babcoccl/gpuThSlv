"""Runs in ``.venv-engine`` and ``.venv-api``.

SpotConfig, ranges, and blockers: pure Python, runs in both runtimes."""

import json

import pytest

from holdem_solver.protocol import BET_SIZE_SETS, SpotConfig, card_id, card_str, combo_str, parse_combo, parse_range

RIVER = ("Ks", "Js", "Th", "7d", "2c")
TURN = RIVER[:4]


def test_card_ids_follow_the_engine_convention():
    assert [card_id(c) for c in ("As", "Ad", "Ac", "Ah", "Ks", "2h")] == [0, 1, 2, 3, 4, 51]
    assert [card_str(i) for i in (0, 19, 29, 50)] == ["As", "Th", "7d", "2c"]
    # Low card id first, as in the engine's infoset keys.
    assert combo_str(parse_combo("9h9d")) == "9d9h"
    assert combo_str(parse_combo("3c4c")) == "4c3c"
    assert combo_str(parse_combo("KdAh")) == "AhKd"


@pytest.mark.parametrize(
    "text,count",
    [
        ("AA", 6),
        ("AKs", 4),
        ("AKo", 12),
        ("AK", 16),
        ("KA", 16),
        ("TT+", 30),
        ("A5s+", 36),
        ("A2s-A5s", 16),
        ("77-TT", 24),
        ("TT-77", 24),
        ("AhKh", 1),
        ("*", 1326),
        ("AA, KK AKs", 16),
        ("AK, AKs", 16),
    ],
)
def test_range_tokens(text, count):
    assert len(parse_range(text)) == count


def test_range_weights_and_overrides():
    weights = parse_range("AK:0.5, AKs:1, QQ:0")
    assert sum(1 for w in weights.values() if w == 1.0) == 4
    assert sum(1 for w in weights.values() if w == 0.5) == 12
    assert len(weights) == 16
    assert parse_range(["AhKh:0.25"]) == {parse_combo("AhKh"): 0.25}


@pytest.mark.parametrize("text", ["", "AAs", "AX", "AKs-AQo", "AK-QJ", "AhAh", "AA:-1", "AA:x", "QQ:0", "AKQ"])
def test_bad_ranges(text):
    with pytest.raises(ValueError):
        parse_range(text)


def test_board_and_dead_cards_remove_combos():
    spot = SpotConfig(RIVER, "KK, AA", "AK", dead_cards=("Ah",))
    resolved = spot.resolve()
    # KK: Ks is on the board, 3 of 6 left. AA: Ah is dead, 3 of 6 left.
    assert len(resolved.hands0) == 6 and len(resolved.removed0) == 6
    # AK: 3 live aces x 3 live kings.
    assert len(resolved.hands1) == 9 and len(resolved.removed1) == 7
    assert all("Ks" not in combo_str(c) and "Ah" not in combo_str(c) for c, _ in resolved.hands0 + resolved.hands1)


def test_blockers_between_ranges_reduce_the_deals():
    resolved = SpotConfig(RIVER, "AhQh, 4c3c", "AhJd, 9d9h").resolve()
    # AhQh cannot be dealt against AhJd.
    assert resolved.num_deals == 3
    assert resolved.blocked_opponent_combos(0) == {"AhQh": 1, "4c3c": 0}
    assert resolved.blocked_opponent_combos(1) == {"AhJd": 1, "9d9h": 0}
    assert resolved.summary()["num_blocked_pairs"] == 1

    aces = SpotConfig(RIVER, "AA", "AKs").resolve()
    # Each AA combo blocks the AKs combos of its two suits: Ad Ac Ah have a live king.
    assert aces.blocked_opponent_combos(0)["AdAc"] == 2 and aces.blocked_opponent_combos(0)["AsAd"] == 1
    assert aces.num_deals == 6 * 3 - (3 * 2 + 3 * 1)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (dict(board=RIVER[:3]), "4 .turn. or 5 .river."),
        (dict(board=RIVER + ("3c",)), "4 .turn. or 5 .river."),
        (dict(board=("Ks", "Ks", "Th", "7d", "2c")), "duplicate"),
        (dict(board=("Ks", "Js", "Th", "7d", "Xx")), "unknown card"),
        (dict(range0="KsKd"), "range0 has no combos left"),
        (dict(range0="AhQh", range1="AhJd"), "shares a card"),
        (dict(pot=0), "positive"),
        (dict(stack=-1), "positive"),
        (dict(bet_sizes="huge"), "unknown bet-size set"),
        (dict(bet_sizes=(0.5, -1.0)), "positive pot fractions"),
        (dict(bet_sizes=()), "at least one bet size"),
        (dict(dead_cards=("Ks",)), "overlap the board"),
        (dict(board=TURN, dead_cards=("2c",)), "only supported on the river"),
        (dict(max_nodes=0), "max_nodes"),
    ],
)
def test_invalid_spots(kwargs, match):
    params = dict(board=RIVER, range0="AA", range1="QQ")
    params.update(kwargs)
    with pytest.raises(ValueError, match=match):
        SpotConfig(**params)


def test_bet_size_sets():
    assert SpotConfig(RIVER, "AA", "QQ").bet_fractions() == BET_SIZE_SETS["standard"] == (0.5, 1.0)
    for name, sizes in BET_SIZE_SETS.items():
        assert SpotConfig(RIVER, "AA", "QQ", bet_sizes=name).bet_fractions() == sizes
        assert list(sizes) == sorted(set(sizes)) and min(sizes) > 0
    spot = SpotConfig(RIVER, "AA", "QQ", pot=20, stack=50, bet_sizes=(1.0, 0.5, 1.0), all_in=True)
    assert spot.bet_fractions() == (0.5, 1.0, 2.5)
    assert SpotConfig(RIVER, "AA", "QQ", pot=20, stack=50, bet_sizes=(), all_in=True).bet_fractions() == (2.5,)


def test_street_and_dict_round_trip():
    spot = SpotConfig("KsJsTh7d", ["AA", "AKs:0.5"], "QQ+", pot=12, stack=88, bet_sizes=[0.75], all_in=True)
    assert spot.street == "turn" and spot.board == TURN and spot.game_name == "spot_turn"
    assert SpotConfig(RIVER, "AA", "QQ", name="x").street == "river"
    wire = json.loads(json.dumps(spot.to_dict()))
    assert SpotConfig.from_dict(wire) == spot
    assert spot.resolve().weighted and not SpotConfig(RIVER, "AA:0.5", "QQ:0.5").resolve().weighted
    with pytest.raises(ValueError, match="unknown spot fields"):
        SpotConfig.from_dict(dict(wire, raises=True))
    with pytest.raises(ValueError, match="missing"):
        SpotConfig.from_dict({"board": list(RIVER)})
