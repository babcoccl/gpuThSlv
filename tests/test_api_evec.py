"""evec vs evwalk equivalence + persisted node-EV roundtrip (API venv only).

``evec.node_action_evs`` must produce the same per-combo per-action
conditional EVs as the shared pure-Python ``evwalk.node_action_evs``
(max |delta| <= 1e-12), including across ``river:`` chance fan-out, and
``load_node_evs`` must roundtrip the ``strategy-<id>.evs.npz`` layout.
"""

import numpy as np

from holdem_solver.api import evec
from holdem_solver.api import nodes as api_nodes
from holdem_solver.api.strategy import spot_ranges
from holdem_solver.protocol.cards import parse_cards
from holdem_solver.protocol.evwalk import (
    TreeIndex,
    node_action_evs,
    reach_at,
    dealt_board,
)

from test_evwalk import RIVER_SPOT, fake_index


def _assert_same(index, hist, reach0, reach1, board_now, pot):
    ref = node_action_evs(index, hist, reach0, reach1, board_now, pot)
    new = evec.node_action_evs(index, hist, reach0, reach1, board_now, pot)
    assert set(new) == set(ref)
    worst = 0.0
    for combo, row in ref.items():
        assert set(new[combo]) == set(row)
        for action, v in row.items():
            worst = max(worst, abs(v - new[combo][action]))
    assert worst <= 1e-12


def test_evec_matches_evwalk_on_fake_river_index():
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    ranges = spot_ranges(spot)
    pot = float(spot["pot"])
    for hist in ((), ("check",), ("check", "b20"), ("b20",)):
        if index.edge_kind(hist) != "player":
            continue
        r0, r1 = reach_at(index, hist, ranges[0], ranges[1], blocked)
        _assert_same(index, hist, r0, r1, dealt_board(RIVER_SPOT.board, hist), pot)


def test_evec_chance_node_and_fold_paths():
    """Synthetic index covering chance fan-out, folds and showdowns."""
    # p0: check|b2 -> check/check = chance -> river:X -> p0 check|b2 -> ...
    hands0 = ["AsKd", "QhJc", "9s8d"]
    hands1 = ["AhKh", "QsQd", "Ts9h"]
    by_node = {}
    by_node[(0, ())] = {h: {"check": 0.6, "b2": 0.4} for h in hands0}
    by_node[(1, ("check",))] = {h: {"check": 0.5, "b2": 0.5} for h in hands1}
    for card in ("Ac", "2c", "7h"):
        hist = ("check", "check", "river:" + card)
        by_node[(0, hist)] = {h: {"check": 0.7, "b2": 0.3} for h in hands0}
        by_node[(1, hist + ("check",))] = {h: {"check": 1.0} for h in hands1}
        by_node[(1, hist + ("b2",))] = {h: {"fold": 0.4, "call": 0.6} for h in hands1}
    by_node[(1, ("b2",))] = {h: {"fold": 0.3, "call": 0.7} for h in hands1}
    by_node[(1, ("check", "b2"))] = {h: {"fold": 0.5, "call": 0.5} for h in hands1}
    index = TreeIndex(by_node)
    board0 = tuple(parse_cards("KsJsTh7d"))  # 4-card turn board
    w0 = {h: 1.0 for h in hands0}
    w1 = {h: 1.0 for h in hands1}
    for hist in ((), ("check",), ("check", "check", "river:Ac"),
                 ("check", "check", "river:2c", "b2")):
        if index.edge_kind(hist) != "player":
            continue
        r0, r1 = reach_at(index, hist, w0, w1, board0)
        _assert_same(index, hist, r0, r1, dealt_board("KsJsTh7d", hist), 20.0)


def test_all_node_evs_covers_every_node_and_matches():
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    ranges = spot_ranges(spot)
    evs, masses = evec.all_node_evs(
        index, tuple(parse_cards(RIVER_SPOT.board)), float(spot["pot"]),
        ranges[0], ranges[1], blocked,
    )
    for (player, hist) in index.by_node:
        assert (player, hist) in evs
        r0, r1 = reach_at(index, hist, ranges[0], ranges[1], blocked)
        ref = node_action_evs(index, hist, r0, r1, dealt_board(RIVER_SPOT.board, hist),
                              float(spot["pot"]))
        got = evs[(player, hist)]
        assert set(got) == set(ref)
        for combo, row in ref.items():
            for a, v in row.items():
                assert abs(v - got[combo][a]) <= 1e-12
            assert masses[(player, hist)][combo] > 0.0


def test_load_node_evs_roundtrip_and_stale_keys(tmp_path):
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    ranges = spot_ranges(spot)
    evs, masses = evec.all_node_evs(
        index, tuple(parse_cards(RIVER_SPOT.board)), float(spot["pot"]),
        ranges[0], ranges[1], blocked,
    )
    # fabricate the npz layout the scheduler writes: one key per
    # (player, hand, hist), values = freqs flattened, ev aligned.
    keys, acts, offsets, values, ev_flat, mass = [], [], [0], [], [], []
    for (player, hist), rows in sorted(index.by_node.items()):
        for hand, freqs in sorted(rows.items()):
            actions = sorted(freqs)
            keys.append("p%d|%s|%s" % (player, hand, "|".join(hist)))
            acts.append(",".join(actions))
            values.extend(freqs.get(a, 0.0) for a in actions)
            row = evs.get((player, hist), {}).get(hand, {})
            ev_flat.extend(row.get(a, 0.0) for a in actions)
            mass.append(masses.get((player, hist), {}).get(hand, 0.0))
            offsets.append(len(values))
    path = tmp_path / "x.evs.npz"
    np.savez(
        path,
        keys=np.array([k.encode() for k in keys], dtype=np.bytes_),
        actions=np.array([a.encode() for a in acts], dtype=np.bytes_),
        offsets=np.array(offsets, dtype=np.int64),
        ev=np.array(ev_flat),
        opp_mass=np.array(mass),
        iteration=np.int64(7),
    )
    loaded = api_nodes.load_node_evs(str(path), keys)
    assert loaded is not None
    got_evs, got_mass = loaded
    for (player, hist), table in evs.items():
        for hand, row in table.items():
            for a, v in row.items():
                assert abs(v - got_evs[hist][hand][a]) <= 1e-12
            assert abs(masses[(player, hist)][hand] - got_mass[hist][hand]) <= 1e-12
    # stale/mismatched keys -> ignored
    assert api_nodes.load_node_evs(str(path), keys[:-1]) is None
    assert api_nodes.load_node_evs(str(path), ["bogus"]) is None
    assert api_nodes.load_node_evs(str(tmp_path / "missing.evs.npz"), keys) is None


def test_compat_all_clamps_cancellation_residue():
    """Every opponent combo conflicting must yield exactly 0 — not positive
    fp residue that callers would divide by."""
    r_opp = np.zeros(evec._N)
    # combo AsKd conflicts: AsQh shares As, KdQc shares Kd.
    # 0.1 + 0.2 - 0.1 - 0.2 leaves ~4e-18 residue without clamping.
    for combo, w in (("AsQh", 0.1), ("KdQc", 0.2)):
        r_opp[evec._gid(combo)] = w
    comp = evec._compat_all(r_opp)
    assert comp[evec._gid("AsKd")] == 0.0
    assert comp.min() == 0.0
    # a genuinely compatible combo keeps its mass
    assert abs(comp[evec._gid("3h4d")] - 0.3) < 1e-12


def test_node_values_uses_persisted_evs():
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    ranges = spot_ranges(spot)
    evs, _ = evec.all_node_evs(
        index, tuple(parse_cards(RIVER_SPOT.board)), float(spot["pot"]),
        ranges[0], ranges[1], blocked,
    )
    flat = {hist: table for (player, hist), table in evs.items()}
    ref = api_nodes.node_values(index, spot, ("check",), node_evs=None)
    got = api_nodes.node_values(index, spot, ("check",), node_evs=flat)
    assert ref["hands"] == got["hands"]
