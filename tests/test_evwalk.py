"""Runs in ``.venv-engine`` and ``.venv-api``.

Shared pure-Python tests for protocol/evwalk.py (collected in both venvs).

The property tests use the FakeEngine's ``fake_real_keys`` strategy: real
HUNL infoset keys on a raise_cap=0 river tree, no GPU needed.
"""

from holdem_solver.engine_worker.fake import FakeEngine
from holdem_solver.protocol import SpotConfig
from holdem_solver.protocol.cards import card_id, combo_str, parse_cards
from holdem_solver.protocol.evwalk import (
    TreeIndex,
    _rank7,
    apply_action,
    initial_reaches,
    node_action_evs,
    parse_history,
    reach_at,
    root_ev,
    rank7,
    walk,
)

RIVER_SPOT = SpotConfig(
    "KsJsTh7d2c", "QQ+, AKs", "TT, AQ", pot=20.0, stack=100.0, bet_sizes="standard"
)


def fake_index():
    """TreeIndex from the FakeEngine's real-keys strategy."""
    engine = FakeEngine().build(RIVER_SPOT.to_dict(), fake_real_keys=True)
    actions = engine.strategy_action_names()
    by_node = {}
    for key, freqs in engine.get_strategy().items():
        player_s, hand, hist_text = key.split("|", 2)
        hist = parse_history(hist_text)
        row = {name: float(freqs[i]) for i, name in enumerate(actions[key])}
        by_node.setdefault((int(player_s[1]), hist), {})[hand] = row
    return TreeIndex(by_node)


def ranges_blocked():
    resolved = RIVER_SPOT.resolve()
    w0 = {combo_str(c): w for c, w in resolved.hands0}
    w1 = {combo_str(c): w for c, w in resolved.hands1}
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    return w0, w1, blocked


def test_apply_action_math():
    chips = apply_action((10.0, 10.0), 0, "check")
    assert chips == (10.0, 10.0)
    chips = apply_action((10.0, 10.0), 0, "b20")
    assert chips == (30.0, 10.0)
    chips = apply_action(chips, 1, "call")
    assert chips == (30.0, 30.0)
    chips = apply_action(chips, 0, "r60")  # r = raiser's total this spot
    assert chips == (60.0, 30.0)


def test_parse_history_forms():
    assert parse_history(None) == ()
    assert parse_history("") == ()
    assert parse_history("check,b10") == ("check", "b10")
    assert parse_history("check,b10|2c|check") == ("check", "b10", "river:2c", "check")
    assert parse_history(["check", "b10"]) == ("check", "b10")
    assert parse_history("banana") == ("banana",)  # lenient; lookup 404s
    try:
        parse_history("river:XX")
        raise AssertionError("should fail")
    except ValueError:
        pass


def test_index_structure():
    index = fake_index()
    assert index.node_at[()] == (0, ["check", "b10", "b20"])
    assert index.node_at[("check",)] == (1, ["check", "b10", "b20"])
    assert index.node_at[("check", "b20")] == (0, ["fold", "call"])
    assert index.edge_kind(("check", "b20")) == "player"
    assert index.edge_kind(("check", "check")) == "terminal"
    assert index.edge_kind(("b10", "call")) == "terminal"


def test_chance_node_detection():
    # Fabricated index: ("check",) has no rows but river children — chance.
    by_node = {(0, ()): {"AsAh": {"check": 1.0}}}
    for dealt in ("river:2c", "river:3d"):
        by_node[(0, ("check", dealt))] = {"AsAh": {"check": 0.5, "b10": 0.5}}
    index = TreeIndex(by_node)
    assert index.edge_kind(("check",)) == "chance"
    assert index.chance_cards[("check",)] == sorted([card_id("2c"), card_id("3d")])
    assert index.node_at[("check", "river:2c")] == (0, ["check", "b10"])


def test_frequencies_sum_to_one():
    index = fake_index()
    for hist, rows in index.rows_at.items():
        for combo, freqs in rows.items():
            assert abs(sum(freqs.values()) - 1.0) < 1e-4, (hist, combo)


def test_reach_consistency_parent_children():
    """child reach = parent reach * strategy(edge) for the acting player."""
    index = fake_index()
    w0, w1, blocked = ranges_blocked()
    for hist, (player, actions) in index.node_at.items():
        parent0, parent1 = reach_at(index, hist, w0, w1, blocked)
        parent = parent0 if player == 0 else parent1
        rows = index.rows_at[hist]
        for action in actions:
            child_hist = hist + (action,)
            if index.edge_kind(child_hist) != "player":
                continue
            child0, child1 = reach_at(index, child_hist, w0, w1, blocked)
            child = child0 if player == 0 else child1
            for combo, freqs in rows.items():
                expected = parent.get(combo, 0.0) * freqs[action]
                got = child.get(combo, 0.0)
                assert abs(expected - got) < 1e-6, (hist, action, combo)


def test_equity_and_ev_bounds():
    index = fake_index()
    w0, w1, blocked = ranges_blocked()
    reach0, reach1 = reach_at(index, (), w0, w1, blocked)
    board_now = tuple(parse_cards(RIVER_SPOT.board))
    evs = node_action_evs(index, (), reach0, reach1, board_now, RIVER_SPOT.pot)
    hands0 = {combo_str(c) for c, _w in RIVER_SPOT.resolve().hands0}
    assert set(evs) <= hands0
    for combo, action_evs in evs.items():
        for _action, ev in action_evs.items():
            assert -200.0 <= ev <= 200.0  # stack-bounded
    root = root_ev(index, w0, w1, board_now, (), RIVER_SPOT.pot)
    assert -50.0 <= root <= 50.0  # pot/2 sunk convention


def test_walk_fold_payoff():
    index = fake_index()
    w0, w1, blocked = ranges_blocked()
    reach0, reach1 = initial_reaches(w0, w1, blocked)
    board_now = tuple(parse_cards(RIVER_SPOT.board))
    # One-node index: p0 always bets 20, the edge is a terminal showdown.
    # AsAh beats QhQd on this board:
    # gross 40 (pot 20 + chips 20+0) minus sunk 30 (c0 + pot/2) = +10.
    by_node = {(0, ()): {"AsAh": {"b20": 1.0}}}
    single = TreeIndex(by_node)
    profit = walk(single, (), (0.0, 0.0), {"AsAh": 1.0}, {"QhQd": 1.0},
                  board_now, 20.0)
    assert abs(profit - 10.0) < 1e-6


def test_rank7_cache_is_bounded():
    """Job export fills the rank cache without viewer requests; it must be
    bounded, not per-board unbounded growth."""
    info = _rank7.cache_info()
    assert info.maxsize is not None and info.maxsize > 0
    assert rank7(tuple(card_id(c) for c in ("As", "Ks", "Qs", "Js", "Ts"))) == rank7(
        (card_id("Ts"), card_id("Js"), card_id("Qs"), card_id("Ks"), card_id("As"))
    )
