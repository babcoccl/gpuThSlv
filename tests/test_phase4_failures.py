"""Runs in ``.venv-api``.

Phase 4: one regression test per failure the Phase 3 scheduler could not
survive (see docs/PHASE4.md, "Failure modes"), plus the scheduler features
that fix them. Fake-engine workers; no GPU needed."""

import os
import signal
from pathlib import Path  # noqa: E402
import socket
import subprocess
import sys
import threading
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402
from holdem_solver.api.scheduler import SchedulerConfig  # noqa: E402
from holdem_solver.api.store import JobStore  # noqa: E402

from phase4_util import body, poll_until, slow, wait_for, worker_pid  # noqa: E402

FAST_WATCHDOG = SchedulerConfig(heartbeat_seconds=0.25, hung_worker_seconds=2.0, cancel_grace_seconds=2.0)


def make_client(tmp_path, gpus=(0,), config=None, **kwargs):
    app = create_app(
        gpus=list(gpus), engine="fake", data_dir=str(tmp_path / "api"),
        scheduler_config=config or SchedulerConfig(heartbeat_seconds=0.5), **kwargs,
    )
    return TestClient(app)


def submit(client, payload, **headers):
    response = client.post("/v1/solves", json=payload, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["id"]


# -- 1. worker OOM mid-build (and mid-solve) -----------------------------------


@pytest.mark.parametrize("fake", [{"fake_oom_on_build": True}, {"fake_oom_at_iteration": 100}])
def test_cuda_oom_fails_job_clearly_and_respawns_worker(tmp_path, fake):
    with make_client(tmp_path) as client:
        before = worker_pid(client, 0)
        job = poll_until(client, submit(client, body(iterations=500, **fake)))
        assert job["status"] == "failed"
        assert job["error_code"] == "cuda_oom"
        assert "CUDA out of memory while " + ("building" if "fake_oom_on_build" in fake else "solving") in job["error"]
        assert "respawned" in job["error"]
        after = worker_pid(client, 0)
        assert after and after != before
        info = client.get("/v1/gpus").json()[0]
        assert info["restarts"] >= 1 and info["healthy"]
        # The GPU is usable again.
        assert poll_until(client, submit(client, body()))["status"] == "done"


# -- 2. API crash mid-solve ---------------------------------------------------


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_api(data_dir, port, gpu):
    env = dict(os.environ)
    env.update(
        HOLDEM_GPUS=str(gpu), HOLDEM_SOLVER_ENGINE="fake", HOLDEM_API_DATA_DIR=data_dir,
        HOLDEM_API_PORT=str(port), HOLDEM_API_KEY="", HOLDEM_HEARTBEAT_SECONDS="0.5",
    )
    proc = subprocess.Popen([sys.executable, "-m", "holdem_solver.api"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import httpx

    http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)

    def up():
        try:
            return http.get("/healthz").status_code == 200
        except httpx.HTTPError:
            assert proc.poll() is None, "api exited during startup"
            return False

    wait_for(up, timeout=60, message="api startup")
    return proc, http


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie (exited, unreaped) counts as gone.
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().split()[2] != "Z"
    except OSError:
        return False


def test_api_crash_mid_solve_fails_running_job_keeps_queue(tmp_path):
    pytest.importorskip("httpx")
    data_dir = str(tmp_path / "api")
    port = _free_port()
    gpu = 7  # its own socket; fake workers ignore CUDA_VISIBLE_DEVICES
    proc, http = _start_api(data_dir, port, gpu)
    try:
        running = http.post("/v1/solves", json=slow()).json()["id"]
        queued = http.post("/v1/solves", json=body(), headers={"Idempotency-Key": "q-1"}).json()["id"]
        wait_for(lambda: http.get(f"/v1/solves/{running}").json()["status"] == "solving", message="solving")
        old_worker = http.get("/v1/gpus").json()[0]["worker_pid"]
        proc.send_signal(signal.SIGKILL)  # the API dies without any cleanup
        proc.wait(10)
        # The orphaned worker notices its parent is gone and exits.
        wait_for(lambda: not _pid_alive(old_worker), timeout=20, message="orphaned worker exit")
    finally:
        if proc.poll() is None:
            proc.kill()
    proc, http = _start_api(data_dir, port, gpu)
    try:
        status = http.get(f"/v1/solves/{running}").json()
        assert status["status"] == "failed"
        assert status["error_code"] == "api_restart"
        assert "API restarted while the job was solving" in status["error"]
        assert "not replayed" in status["error"]
        # Not replayed: it stays failed.
        time.sleep(1.5)
        assert http.get(f"/v1/solves/{running}").json()["status"] == "failed"
        # The persistent queue kept the queued job, and it runs now.
        wait_for(lambda: http.get(f"/v1/solves/{queued}").json()["status"] == "done", message="queued job runs")
        # Its idempotency key still maps to it.
        again = http.post("/v1/solves", json=body(), headers={"Idempotency-Key": "q-1"})
        assert again.status_code == 202 and again.json()["id"] == queued and again.json()["replayed"]
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(30)


# -- 3. hung worker -----------------------------------------------------------


def test_hung_worker_is_killed_respawned_and_job_failed(tmp_path):
    with make_client(tmp_path, config=FAST_WATCHDOG) as client:
        job_id = submit(client, slow())
        poll_until(client, job_id, statuses=("solving",))
        hung = worker_pid(client, 0)
        os.kill(hung, signal.SIGSTOP)  # alive but never answers again
        t0 = time.time()
        job = poll_until(client, job_id, timeout=30)
        detect = time.time() - t0
        assert job["status"] == "failed" and job["error_code"] == "worker_hung", job
        assert "stopped answering ping" in job["error"]
        assert detect < 2.0 + 5.0
        assert not _pid_alive(hung)
        fresh = worker_pid(client, 0)
        assert fresh and fresh != hung
        assert poll_until(client, submit(client, body()))["status"] == "done"


def test_job_timeout_while_build_hangs_kills_worker(tmp_path):
    with make_client(tmp_path, config=FAST_WATCHDOG) as client:
        before = worker_pid(client, 0)
        # The build blocks the engine thread but ping still answers: only the
        # per-job timeout can catch this.
        job = poll_until(client, submit(client, body(timeout=1.0, fake_build_seconds=60)), timeout=30)
        assert job["status"] == "failed" and job["error_code"] == "timeout", job
        assert "timeout_seconds=1 while building" in job["error"]
        assert worker_pid(client, 0) != before
        assert poll_until(client, submit(client, body()))["status"] == "done"


def test_job_timeout_while_solving_cancels_and_keeps_partial_strategy(tmp_path):
    with make_client(tmp_path, config=FAST_WATCHDOG) as client:
        before = worker_pid(client, 0)
        job = poll_until(client, submit(client, slow(timeout=1.5)), timeout=30)
        assert job["status"] == "failed" and job["error_code"] == "timeout", job
        assert job["strategy_available"]
        assert client.get(f"/v1/solves/{job['id']}/strategy").status_code == 200
        assert worker_pid(client, 0) == before  # cooperative cancel; no kill needed


# -- 4. disk full when saving a strategy ----------------------------------------


def test_disk_full_on_strategy_save(tmp_path):
    with make_client(tmp_path) as client:
        scheduler = client.app.state.scheduler
        # Real ENOSPC from the kernel: every write to /dev/full fails.
        scheduler._open_for_write = lambda path: open("/dev/full", "wb")
        job = poll_until(client, submit(client, body()))
        assert job["status"] == "failed" and job["error_code"] == "disk_full", job
        assert "No space left on device" in job["error"]
        assert not job["strategy_available"]
        assert [p for p in os.listdir(tmp_path / "api") if p.startswith("strategy-")] == []
        assert client.get("/v1/gpus").json()[0]["state"] == "unbuilt"  # solver released
        scheduler._open_for_write = lambda path: open(path, "wb")
        assert poll_until(client, submit(client, body()))["status"] == "done"


# -- 5. two clients cancelling the same job --------------------------------------


def _concurrent(n, fn):
    barrier = threading.Barrier(n)
    results = [None] * n

    def run(i):
        barrier.wait()
        results[i] = fn(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_clients_cancel_same_running_job(tmp_path):
    with make_client(tmp_path) as a:
        b = TestClient(a.app)  # a second HTTP client; no second lifespan
        job_id = submit(a, slow())
        poll_until(a, job_id, statuses=("solving",))
        responses = _concurrent(2, lambda i: (a, b)[i].delete(f"/v1/solves/{job_id}"))
        assert [r.status_code for r in responses] == [200, 200]
        job = poll_until(a, job_id)
        assert job["status"] == "cancelled" and job["strategy_available"]
        # A third cancel after the fact is a harmless no-op.
        assert a.delete(f"/v1/solves/{job_id}").json()["status"] == "cancelled"
        assert poll_until(a, submit(a, body()))["status"] == "done"


def test_two_clients_cancel_same_queued_job(tmp_path):
    with make_client(tmp_path) as client:
        blocker = submit(client, slow())
        queued = submit(client, body())
        responses = _concurrent(2, lambda i: client.delete(f"/v1/solves/{queued}"))
        assert {r.json()["status"] for r in responses} == {"cancelled"}
        client.delete(f"/v1/solves/{blocker}")
        poll_until(client, blocker)
        time.sleep(1.5)
        job = client.get(f"/v1/solves/{queued}").json()
        assert job["status"] == "cancelled" and job["started_at"] is None  # never dispatched


def test_cancel_vs_claim_race_has_one_winner(tmp_path):
    """Phase 3's DELETE read 'queued', then wrote 'cancelled' even if the
    scheduler had claimed the job in between, leaving a 'cancelled' job
    running on a GPU. Store.cancel/claim are now atomic transitions."""
    store = JobStore(str(tmp_path / "race.db"))
    for _ in range(200):
        job, _ = store.create({"board": "x"}, {})
        claimed, _ = _concurrent(2, lambda i, j=job["id"]: store.claim(j, 0) if i == 0 else store.cancel(j))
        row = store.get(job["id"])
        if claimed:
            assert row["status"] == "building" and row["cancel_requested"] and row["gpu"] == 0
        else:
            assert row["status"] == "cancelled" and row["gpu"] is None and row["started_at"] is None
    store.close()


# -- scheduler features ----------------------------------------------------------


def test_priority_order(tmp_path):
    with make_client(tmp_path) as client:
        blocker = submit(client, slow())
        poll_until(client, blocker, statuses=("solving",))
        low1 = submit(client, body(priority=0))
        low2 = submit(client, body(priority=0))
        high = submit(client, body(priority=5))
        client.delete(f"/v1/solves/{blocker}")
        jobs = [poll_until(client, j) for j in (high, low1, low2)]
        assert all(j["status"] == "done" for j in jobs)
        starts = [j["started_at"] for j in jobs]
        assert starts == sorted(starts), "high priority first, then FIFO"


def test_queue_depth_429_and_idempotent_replay_when_full(tmp_path):
    with make_client(tmp_path, max_queue_depth=2) as client:
        blocker = submit(client, slow())
        poll_until(client, blocker, statuses=("solving",))
        first = submit(client, body(), **{"Idempotency-Key": "k1"})
        submit(client, body(iterations=300))
        full = client.post("/v1/solves", json=body(iterations=400))
        assert full.status_code == 429 and full.headers["retry-after"] == "30"
        assert "max_queue_depth=2" in full.json()["detail"]
        replay = client.post("/v1/solves", json=body(), headers={"Idempotency-Key": "k1"})
        assert replay.status_code == 202 and replay.json()["id"] == first
        client.delete(f"/v1/solves/{blocker}")


def test_idempotency_keys(tmp_path):
    with make_client(tmp_path) as client:
        ids = _concurrent(4, lambda i: client.post("/v1/solves", json=body(), headers={"Idempotency-Key": "same"}))
        assert {r.status_code for r in ids} == {202}
        assert len({r.json()["id"] for r in ids}) == 1
        assert sum(r.json()["replayed"] for r in ids) == 3
        conflict = client.post("/v1/solves", json=body(iterations=999), headers={"Idempotency-Key": "same"})
        assert conflict.status_code == 409
        assert len(client.app.state.store.all_jobs()) == 1


def test_vram_aware_dispatch_uses_measured_peaks(tmp_path):
    reserve = 64 * 1024**2
    with make_client(tmp_path / "probe") as probe:
        accepted = probe.post("/v1/solves", json=body()).json()
        predicted = accepted["predicted_vram_bytes"]
        estimate = probe.app.state.store.get(accepted["id"])["estimated_vram_bytes"]
    assert predicted == int(estimate * 1.6)  # seed factor: no samples yet
    config = SchedulerConfig(
        heartbeat_seconds=0.5, vram_reserve_bytes=reserve,
        gpu_vram_bytes={0: reserve + predicted // 2, 1: reserve + 2 * estimate},
    )
    with make_client(tmp_path, gpus=(0, 1), config=config) as client:
        client.app.state.scheduler.vram.min_sample_bytes = 0
        jobs = [poll_until(client, submit(client, body(fake_peak_vram_bytes=estimate))) for _ in range(2)]
        assert client.app.state.scheduler.vram.factor("float32")[1:] == ("seed", 2)
        assert [j["gpu"] for j in jobs] == [1, 1], "gpu 0 is too small and is skipped even when idle"
        # A third job (the 3rd sample) measures a peak of 3x its estimate ...
        peaky = poll_until(client, submit(client, body(fake_peak_vram_bytes=3 * estimate)))
        assert peaky["peak_vram_bytes"] == 3 * estimate
        # ... so the model now predicts 3 x 1.05 x estimate, which fits no GPU.
        rejected = client.post("/v1/solves", json=body(iterations=123))
        assert rejected.status_code == 422
        assert "x3.150 from 3 measured peak(s)" in rejected.json()["detail"], rejected.json()


def test_strategy_retention_cleanup(tmp_path):
    config = SchedulerConfig(heartbeat_seconds=0.5, retention_seconds=3600)
    with make_client(tmp_path, config=config) as client:
        job = poll_until(client, submit(client, body()))
        path = client.app.state.store.get(job["id"])["strategy_path"]
        assert os.path.exists(path)
        data_dir = tmp_path / "api"
        old_orphan = data_dir / "strategy-deadbeef.npz"
        old_orphan.write_bytes(b"x")
        os.utime(old_orphan, (time.time() - 7200, time.time() - 7200))
        new_orphan = data_dir / "strategy-cafebabe.npz.tmp"
        new_orphan.write_bytes(b"x")
        # the .evs.npz companion of a live strategy is not an orphan —
        # deleting it would degrade /node to the slow fallback walk
        evs = Path(path).with_suffix(".evs.npz")
        evs.write_bytes(b"x")
        os.utime(evs, (time.time() - 7200, time.time() - 7200))
        scheduler = client.app.state.scheduler
        assert scheduler.cleanup_strategies(time.time()) == {"expired": 0, "removed": 0, "orphans": 1}
        assert os.path.exists(path) and evs.exists() and new_orphan.exists() and not old_orphan.exists()
        result = scheduler.cleanup_strategies(time.time() + 7200)
        assert result["expired"] == 1 and result["removed"] == 1
        assert not os.path.exists(path) and not evs.exists()
        assert client.get(f"/v1/solves/{job['id']}/strategy").status_code == 410
        status = client.get(f"/v1/solves/{job['id']}").json()
        assert not status["strategy_available"] and status["strategy_expired_at"]


def test_small_jobs_do_not_evict_large_vram_samples(tmp_path):
    from holdem_solver.api.vram import VramModel

    store = JobStore(str(tmp_path / "vram.db"))
    big = 256 * 1024**2

    def finished(estimate, peak, t):
        job, _ = store.create({"board": "x"}, {}, dtype="float32")
        with store._db:
            store._db.execute(
                "UPDATE jobs SET status='done', estimated_vram_bytes=?, peak_vram_bytes=?, finished_at=? WHERE id=?",
                (estimate, peak, t, job["id"]),
            )

    for t, ratio in enumerate((1.5, 1.2, 1.3)):
        finished(big, int(big * ratio), float(t))
    for i in range(60):  # newer small solves, below the 64 MiB sample floor
        finished(8 * 1024**2, 6 * 1024**2, 10.0 + i)
    factor, source, samples = VramModel(store).factor("float32")
    assert (source, samples) == ("measured", 3)
    assert factor == pytest.approx(1.5 * 1.05)
