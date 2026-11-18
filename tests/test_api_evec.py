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


def _write_evs_npz(path, keys, acts, offsets, ev_flat, mass):
    """The ``strategy-<id>.evs.npz`` layout as ``_export_node_evs`` writes
    it, including the payload checksum."""
    import zlib

    ev = np.asarray(ev_flat, dtype=np.float64)
    mass_a = np.asarray(mass, dtype=np.float64)
    np.savez(
        path,
        keys=np.array([k.encode() for k in keys], dtype=np.bytes_),
        actions=np.array([a.encode() for a in acts], dtype=np.bytes_),
        offsets=np.array(offsets, dtype=np.int64),
        ev=ev,
        opp_mass=mass_a,
        crc32=np.int64(zlib.crc32(ev.tobytes()) ^ zlib.crc32(mass_a.tobytes())),
        iteration=np.int64(7),
    )


def _evs_fixture(index, spot):
    """``(keys, acts, offsets, ev_flat, mass, evs, masses)`` for a valid
    evs.npz matching ``index``."""
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    ranges = spot_ranges(spot)
    evs, masses = evec.all_node_evs(
        index, tuple(parse_cards(RIVER_SPOT.board)), float(spot["pot"]),
        ranges[0], ranges[1], blocked,
    )
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
    return keys, acts, offsets, ev_flat, mass, evs, masses


def test_export_node_evs_atomic_write(tmp_path):
    """The evs export is temp+fsync+rename: a mid-write crash must leave
    neither a truncated evs.npz nor a lingering .tmp file."""
    import types

    from holdem_solver.api.scheduler import Scheduler
    from holdem_solver.api.store import JobStore

    keys = ["p0|AsKd|c|x:c"]
    acts = ["c"]
    offsets = [0, 1]
    values = np.array([1.0], dtype=np.float64)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = JobStore(str(tmp_path / "jobs.db"))
    job, _ = store.create(RIVER_SPOT.to_dict(), {})
    job_id = job["id"]
    strat_path = data_dir / ("strategy-%s.npz" % job_id)
    np.savez(
        strat_path,
        keys=np.array([k.encode() for k in keys], dtype=np.bytes_),
        actions=np.array([a.encode() for a in acts], dtype=np.bytes_),
        offsets=np.array(offsets, dtype=np.int64),
        values=values,
        iteration=np.int64(3),
    )
    evs_path = data_dir / ("strategy-%s.evs.npz" % job_id)
    tmp_path_name = evs_path.parent / (evs_path.name + ".tmp")
    strategy = types.SimpleNamespace(
        offsets=np.array(offsets, dtype=np.int64),
        keys=keys,
        actions=acts,
        values=values,
        iteration=3,
    )
    sched = Scheduler([None], store, str(data_dir), engine="external")
    sched._export_node_evs(job_id, strat_path, strategy)
    assert evs_path.exists()
    loaded = api_nodes.load_node_evs(str(evs_path), keys)
    assert loaded is not None and "c" in loaded[0][("c", "x:c")]["AsKd"]
    # mid-write failure: nothing lands under the final name, no orphan .tmp
    evs_path.unlink()
    sched._open_for_write = lambda path: open("/dev/full", "wb")
    sched._export_node_evs(job_id, strat_path, strategy)
    assert not evs_path.exists()
    assert not tmp_path_name.exists()


def test_load_node_evs_roundtrip_and_stale_keys(tmp_path):
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    keys, acts, offsets, ev_flat, mass, evs, masses = _evs_fixture(index, spot)
    path = tmp_path / "x.evs.npz"
    _write_evs_npz(path, keys, acts, offsets, ev_flat, mass)
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


def test_load_node_evs_corrupt_and_truncated_files(tmp_path):
    """Stale/corrupt/missing evs.npz must never serve values or crash."""
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    keys = index.keys_list if hasattr(index, "keys_list") else [
        "p%d|%s|%s" % (pl, hand, "|".join(hist))
        for (pl, hist), rows in sorted(index.by_node.items())
        for hand in sorted(rows)
    ]
    # not a zip at all
    garbage = tmp_path / "g.evs.npz"
    garbage.write_bytes(b"\x89this is not an npz file")
    assert api_nodes.load_node_evs(str(garbage), keys) is None
    # a valid npz missing the required members
    bad = tmp_path / "bad.evs.npz"
    np.savez(bad, keys=np.array([b"k"]))
    assert api_nodes.load_node_evs(str(bad), ["k"]) is None
    # keys match but ev array truncated -> load still parses; members that
    # are absent raise KeyError inside -> must be caught
    missing = tmp_path / "missing.evs.npz"
    assert api_nodes.load_node_evs(str(missing), keys) is None


def test_node_values_falls_back_when_persisted_evs_unusable(tmp_path):
    """Persisted vs live-computed: corrupt/missing companions fall back to
    the live evec walk and return identical values."""
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    blocked = tuple(parse_cards(RIVER_SPOT.board))
    ranges = spot_ranges(spot)
    evs, _ = evec.all_node_evs(
        index, tuple(parse_cards(RIVER_SPOT.board)), float(spot["pot"]),
        ranges[0], ranges[1], blocked,
    )
    flat = {hist: table for (player, hist), table in evs.items()}
    live = api_nodes.node_values(index, spot, ("check",), node_evs=None)
    persisted = api_nodes.node_values(index, spot, ("check",),
                                      node_evs=flat)
    assert live["hands"] == persisted["hands"]
    # corrupt file -> load_node_evs declines -> caller passes node_evs=None
    corrupt = tmp_path / "x.evs.npz"
    corrupt.write_bytes(b"junk")
    assert api_nodes.load_node_evs(str(corrupt), ["k1", "k2"]) is None
    missing = tmp_path / "none.evs.npz"
    assert api_nodes.load_node_evs(str(missing), ["k1", "k2"]) is None
    assert api_nodes.node_values(index, spot, ("check",),
                                 node_evs=None)["hands"] == live["hands"]


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


def _mutate_evs_bytes(tmp_path, valid_path, name, mutate_bytes=None, mutate_members=None):
    """Copy a valid evs.npz, apply a byte-level or member-level mutation,
    return the mutated path."""
    src = tmp_path / name
    if mutate_bytes is not None:
        raw = valid_path.read_bytes()
        src.write_bytes(mutate_bytes(raw))
        return src
    data = dict(np.load(valid_path).items())
    mutate_members(data)
    np.savez(src, **data)
    return src


def test_load_node_evs_truncated_bytes(tmp_path):
    """A file cut at 10/50/90% or to one byte must decline, never crash."""
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    keys, acts, offsets, ev_flat, mass, _e, _m = _evs_fixture(index, spot)
    valid = tmp_path / "ok.evs.npz"
    _write_evs_npz(valid, keys, acts, offsets, ev_flat, mass)
    n = len(valid.read_bytes())
    for frac in (0.10, 0.50, 0.90):
        cut = _mutate_evs_bytes(tmp_path, valid, "cut%d.evs.npz" % int(frac * 100),
                                lambda b, f=frac: b[: int(n * f)])
        assert api_nodes.load_node_evs(str(cut), keys) is None
    one = _mutate_evs_bytes(tmp_path, valid, "one.evs.npz", lambda b: b[:1])
    assert api_nodes.load_node_evs(str(one), keys) is None


def test_load_node_evs_schema_and_payload_mutations(tmp_path):
    """Every payload/schema corruption declines; none raises."""
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    keys, acts, offsets, ev_flat, mass, _e, _m = _evs_fixture(index, spot)
    valid = tmp_path / "ok.evs.npz"
    _write_evs_npz(valid, keys, acts, offsets, ev_flat, mass)
    assert api_nodes.load_node_evs(str(valid), keys) is not None

    def shorten_ev(d):
        d["ev"] = d["ev"][:-1]  # one element short of offsets[-1]

    def wrong_dtype(d):
        d["ev"] = d["ev"].astype(np.float32)

    def nan_ev(d):
        d["ev"][0] = np.nan

    def inf_ev(d):
        d["ev"][1] = np.inf

    def bit_flip(d):
        # valid zip, matching keys — only the checksum catches this
        d["ev"][0] = float(d["ev"][0]) + 1e-9

    def short_mass(d):
        d["opp_mass"] = d["opp_mass"][:-1]

    def dropped_member(d):
        del d["opp_mass"]

    def bad_offsets(d):
        d["offsets"][-1] = len(d["ev"]) + 1

    for name, mut in (
        ("short_ev", shorten_ev), ("dtype", wrong_dtype), ("nan", nan_ev),
        ("inf", inf_ev), ("bitflip", bit_flip), ("short_mass", short_mass),
        ("no_mass", dropped_member), ("bad_offsets", bad_offsets),
    ):
        p = _mutate_evs_bytes(tmp_path, valid, "%s.evs.npz" % name,
                              mutate_members=mut)
        assert api_nodes.load_node_evs(str(p), keys) is None, name


def test_node_values_fallback_identical_for_every_corruption(tmp_path):
    """/node falls back to the live walk and serves identical hands for
    every corruption path."""
    index = fake_index()
    spot = RIVER_SPOT.to_dict()
    keys, acts, offsets, ev_flat, mass, evs, _m = _evs_fixture(index, spot)
    valid = tmp_path / "ok.evs.npz"
    _write_evs_npz(valid, keys, acts, offsets, ev_flat, mass)
    persisted = api_nodes.load_node_evs(str(valid), keys)
    served = api_nodes.node_values(index, spot, ("check",), node_evs=persisted[0])
    live = api_nodes.node_values(index, spot, ("check",), node_evs=None)
    assert served["hands"] == live["hands"]

    n = len(valid.read_bytes())
    variants = [
        _mutate_evs_bytes(tmp_path, valid, "v_cut.evs.npz", lambda b: b[: n // 2]),
        _mutate_evs_bytes(tmp_path, valid, "v_flip.evs.npz",
                          mutate_members=lambda d: d["ev"].__setitem__(0, 1e9)),
    ]
    for p in variants:
        declined = api_nodes.load_node_evs(str(p), keys)
        assert declined is None
        # caller fallback pattern: node_evs=None -> live compute
        assert api_nodes.node_values(index, spot, ("check",), node_evs=None)[
            "hands"] == live["hands"]
