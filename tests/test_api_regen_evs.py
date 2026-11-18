"""Runs in ``.venv-api``.

``python -m holdem_solver.api.regen_evs [--job ID | --all]`` — the
offline migration that re-persists declined ``strategy-<id>.evs.npz``
files. It must regenerate only jobs whose strategy file still exists,
skip expired jobs, be idempotent, write through the single atomic
writer, and lose the race against a concurrent retention cleanup (no
companion written for a job expired mid-compute).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from holdem_solver.api import nodes as api_nodes
from holdem_solver.api.regen_evs import main, regen_job
from holdem_solver.api.store import JobStore

from test_api_phase9 import make_client, solved_job


def _solved(tmp_path):
    """Finish one REAL_SOLVE job; return ``(job_id, strat, evs)``."""
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = solved_job(client)
    strat = tmp_path / "api" / f"strategy-{job_id}.npz"
    evs = tmp_path / "api" / f"strategy-{job_id}.evs.npz"
    assert strat.exists() and evs.exists()
    return job_id, strat, evs


def _store(tmp_path):
    return JobStore(str(tmp_path / "api" / "solves.db"))


def test_regen_job_rewrites_missing_and_declined(tmp_path):
    job_id, strat, evs = _solved(tmp_path)
    keys = api_nodes.load_index(str(strat))[0].keys_list
    original = evs.read_bytes()
    evs.unlink()

    store = _store(tmp_path)
    job = store.get(job_id)
    assert regen_job(job, store)
    assert evs.exists()
    pair = api_nodes.load_node_evs(str(evs), keys)
    assert pair is not None, "regenerated file does not load"
    store.close()

    # corrupt/stale files get rewritten identically — idempotent
    evs.write_bytes(b"junk")
    assert api_nodes.load_node_evs(str(evs), keys) is None
    store = _store(tmp_path)
    assert regen_job(store.get(job_id), store)
    assert evs.read_bytes() == original
    assert regen_job(store.get(job_id), store)
    assert evs.read_bytes() == original
    store.close()


def test_regen_skips_expired_and_missing_strategy(tmp_path):
    job_id, strat, evs = _solved(tmp_path)
    evs.unlink()
    store = _store(tmp_path)

    # expired job: nothing written
    store.set_fields(job_id, strategy_expired_at=time.time())
    assert regen_job(store.get(job_id), store) is False
    assert not evs.exists()

    # strategy file deleted but not yet marked expired: also skipped
    store.set_fields(job_id, strategy_expired_at=None)
    strat.unlink()
    assert regen_job(store.get(job_id), store) is False
    assert not evs.exists()
    store.close()


def test_regen_all_counts_and_cli(tmp_path, capsys):
    job_a, strat_a, evs_a = _solved(tmp_path)
    job_b, _strat_b, evs_b = _solved(tmp_path)
    evs_a.unlink()
    evs_b.unlink()
    store = _store(tmp_path)
    store.set_fields(job_b, strategy_expired_at=time.time())
    store.close()

    rc = main(["--data-dir", str(tmp_path / "api"), "--all"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "regenerated=1" in out and "skipped=1" in out
    assert evs_a.exists() and not evs_b.exists()

    # --job on an unknown id just skips
    rc = main(["--data-dir", str(tmp_path / "api"), "--job", "nope"])
    assert rc == 0
    assert "regenerated=0" in capsys.readouterr().out


def test_regen_loses_race_with_retention_cleanup(tmp_path):
    """A job expired between selection and the write must not get a
    companion file: regen re-checks the store right before writing."""
    job_id, strat, evs = _solved(tmp_path)
    evs.unlink()
    store = _store(tmp_path)

    orig_export = api_nodes.export_node_evs

    def expire_mid_compute(index, spot):
        result = orig_export(index, spot)
        # concurrent retention cleanup: strategy gone, job expired
        strat.unlink()
        store.set_fields(job_id, strategy_path=None,
                         strategy_expired_at=time.time())
        return result

    api_nodes.export_node_evs = expire_mid_compute
    try:
        assert regen_job(store.get(job_id), store) is False
    finally:
        api_nodes.export_node_evs = orig_export
    assert not evs.exists()
    store.close()


def test_regen_loses_race_during_publish(tmp_path):
    """A job expired between the pre-write checks and os.replace must
    not keep the just-published companion: regen reconciles after the
    write and removes it."""
    job_id, strat, evs = _solved(tmp_path)
    evs.unlink()
    store = _store(tmp_path)

    orig_write = api_nodes.write_node_evs

    def expire_mid_write(*a, **kw):
        # concurrent retention cleanup during the write: strategy gone,
        # job expired — the publication still lands
        strat.unlink()
        store.set_fields(job_id, strategy_path=None,
                         strategy_expired_at=time.time())
        orig_write(*a, **kw)

    api_nodes.write_node_evs = expire_mid_write
    try:
        assert regen_job(store.get(job_id), store) is False
    finally:
        api_nodes.write_node_evs = orig_write
    assert not evs.exists(), "expired job regained a companion file"
    store.close()


def test_regen_writers_do_not_share_tmp_path(tmp_path):
    """Overlapping migrations on one job write distinct temp files —
    a shared deterministic .tmp would let one writer truncate the
    other's file between write and os.replace."""
    job_id, strat, evs = _solved(tmp_path)
    evs.unlink()
    store = _store(tmp_path)
    tmps = []

    orig_write = api_nodes.write_node_evs

    def spy(*a, **kw):
        tmps.append(str(kw.get("tmp_path")))
        orig_write(*a, **kw)

    api_nodes.write_node_evs = spy
    try:
        assert regen_job(store.get(job_id), store)
        evs.unlink()
        assert regen_job(store.get(job_id), store)
    finally:
        api_nodes.write_node_evs = orig_write
    assert len(tmps) == 2 and tmps[0] != tmps[1]
    assert all(t.endswith(".tmp") for t in tmps)
    assert all(not Path(t).exists() for t in tmps)  # cleaned up
    pair = api_nodes.load_node_evs(str(evs),
                                   api_nodes.load_index(str(strat))[0].keys_list)
    assert pair is not None, "final destination is not a valid archive"
    store.close()


def test_orphan_sweep_deletes_companion_regardless_of_mtime(tmp_path):
    """A .evs.npz whose strategy file doesn't exist is deleted even when
    it is brand-new — a companion can never outlive its strategy."""
    from holdem_solver.api.scheduler import Scheduler

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = JobStore(str(tmp_path / "jobs.db"))
    orphan = data_dir / "strategy-deadbeef.evs.npz"
    orphan.write_bytes(b"x")
    os.utime(orphan, None)  # fresh mtime — must still be removed
    # a .npz orphan with a fresh mtime is still protected by age
    young = data_dir / "strategy-cafe.npz"
    young.write_bytes(b"x")
    scheduler = Scheduler([None], store, str(data_dir), engine="external")
    result = scheduler.cleanup_strategies(time.time())
    assert result["orphans"] >= 1, result
    assert not orphan.exists()
    assert young.exists()
    store.close()
