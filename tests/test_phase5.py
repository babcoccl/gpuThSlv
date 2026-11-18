"""Runs in ``.venv-api``.

Phase 5: VRAM seed, /metrics, security limits, API polish, external
(systemd-style) workers. Fake-engine workers; no GPU needed."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402
from holdem_solver.api import security  # noqa: E402
from holdem_solver.api.scheduler import SchedulerConfig  # noqa: E402
from holdem_solver.api.store import JobStore  # noqa: E402
from holdem_solver.api.strategy import hand_class, hand_classes, hand_grid, map_strategy  # noqa: E402
from holdem_solver.api.vram import MIN_SAMPLES, SEED_FACTOR, VramModel  # noqa: E402

from phase4_util import body, poll_until, slow, wait_for, worker_pid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024**2


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


# -- 1. VRAM seed -----------------------------------------------------------------


def _finished(store, estimate, peak, t, dtype="float32"):
    job, _ = store.create({"board": "x"}, {}, dtype=dtype)
    with store._db:
        store._db.execute(
            "UPDATE jobs SET status='done', estimated_vram_bytes=?, peak_vram_bytes=?, finished_at=? WHERE id=?",
            (estimate, peak, t, job["id"]),
        )


def test_vram_seed_until_three_large_samples(tmp_path):
    store = JobStore(str(tmp_path / "v.db"))
    model = VramModel(store)
    assert (SEED_FACTOR, MIN_SAMPLES) == (1.6, 3)
    assert model.factor("float32") == (1.6, "seed", 0)
    for i in range(20):  # small spots never count
        _finished(store, 10 * MIB, 30 * MIB, i)
    assert model.factor("float32") == (1.6, "seed", 0)
    _finished(store, 100 * MIB, 110 * MIB, 100)
    _finished(store, 200 * MIB, 230 * MIB, 101)
    assert model.factor("float32") == (1.6, "seed", 2), "two samples keep the seed"
    assert model.predict(100 * MIB) == int(100 * MIB * 1.6)
    _finished(store, 64 * MIB, 70 * MIB, 102)
    factor, source, samples = model.factor("float32")
    assert (source, samples) == ("measured", 3)
    assert factor == pytest.approx(1.15 * 1.05)
    assert model.factor("float64") == (1.6, "seed", 0), "samples are per dtype"


# -- 2. /metrics --------------------------------------------------------------------


def _metric(text, name, **labels):
    want = ",".join('%s="%s"' % kv for kv in sorted(labels.items()))
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        if key == (name if not labels else "%s{%s}" % (name, want)):
            return float(value)
    return None


def test_metrics_endpoint(tmp_path):
    with make_client(tmp_path, gpus=(0, 1), api_key="k") as client:
        auth = {"Authorization": "Bearer k"}
        assert client.get("/metrics").status_code == 401
        done = poll_until_auth(client, submit(client, body(), **auth), auth)
        assert done["status"] == "done"
        failed = poll_until_auth(client, submit(client, body(fake_fail_solve=True), **auth), auth)
        assert failed["error_code"] == "engine_error"
        os.kill(worker_pid_auth(client, 0, auth), signal.SIGKILL)
        wait_for(lambda: client.app.state.scheduler.gpu_metrics()[0]["restarts"] >= 1, message="restart counted")
        response = client.get("/metrics", headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert _metric(text, "holdem_jobs", status="done") == 1
    assert _metric(text, "holdem_jobs", status="failed") == 1
    assert _metric(text, "holdem_jobs_failed", error_code="engine_error") == 1
    assert _metric(text, "holdem_jobs_failed", error_code="cuda_oom") == 0
    assert _metric(text, "holdem_queue_depth") == 0
    assert _metric(text, "holdem_vram_factor", dtype="float32", source="seed") == 1.6
    assert _metric(text, "holdem_worker_restarts_total", gpu="0") >= 1
    assert sum(_metric(text, "holdem_build_duration_seconds_count", gpu=g, dtype="float32") or 0
               for g in ("0", "1")) == 2
    assert sum(_metric(text, "holdem_solve_duration_seconds_count", gpu=g, status="done") or 0
               for g in ("0", "1")) == 1
    assert any(_metric(text, "holdem_iterations_per_second", gpu=g) for g in ("0", "1"))
    assert "holdem_gpu_utilization_ratio" in text and "holdem_gpu_memory_used_bytes" in text


def poll_until_auth(client, job_id, headers, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/v1/solves/{job_id}", headers=headers).json()
        if status["status"] in ("done", "cancelled", "failed"):
            return status
        time.sleep(0.1)
    raise AssertionError(status)


def worker_pid_auth(client, gpu, headers):
    return next(i["worker_pid"] for i in client.get("/v1/gpus", headers=headers).json() if i["gpu"] == gpu)


# -- 4. security ----------------------------------------------------------------------


def test_key_compare_is_constant_time_helper():
    assert security.key_matches("abc", "abc")
    assert not security.key_matches("abd", "abc")
    assert not security.key_matches(None, "abc")
    assert not security.key_matches("", "abc")


def test_post_rate_limit(tmp_path):
    with make_client(tmp_path, post_rate_per_minute=6, post_burst=2) as client:
        assert client.post("/v1/solves", json=body()).status_code == 202
        assert client.post("/v1/solves", json=body()).status_code == 202
        limited = client.post("/v1/solves", json=body())
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 10
        assert "rate limit" in limited.json()["detail"]
        assert client.get("/healthz").status_code == 200, "GETs are not limited"
        # Separate keys get separate buckets.
        assert client.post("/v1/solves", json=body(), headers={"X-API-Key": "other"}).status_code == 202


def test_rate_limiter_refills():
    now = [0.0]
    limiter = security.RateLimiter(60, 1, clock=lambda: now[0])
    assert limiter.take("a") == 0
    assert limiter.take("a") == pytest.approx(1.0)
    now[0] = 1.01
    assert limiter.take("a") == 0


def test_request_body_limit(tmp_path):
    with make_client(tmp_path, max_body_bytes=2048) as client:
        big = dict(body(), name="x" * 150, range0="QQ+, AKs" + ", AKs" * 600)
        response = client.post("/v1/solves", json=big)
        assert response.status_code == 413, response.text

        def chunks():
            yield b'{"board": "KsJsTh7d2c", "range0": "'
            for _ in range(10):
                yield b"A" * 500
            yield b'"}'

        streamed = client.post("/v1/solves", content=chunks(), headers={"content-type": "application/json"})
        assert streamed.status_code == 413, streamed.text
        assert client.post("/v1/solves", json=body()).status_code == 202


def test_field_length_limits(tmp_path):
    with make_client(tmp_path, max_body_bytes=0) as client:
        response = client.post("/v1/solves", json=dict(body(), range0="A" * 5000))
        assert response.status_code == 422


def test_socket_and_shm_are_private(tmp_path):
    with make_client(tmp_path) as client:
        sched = client.app.state.scheduler
        c = sched.client(0)
        for path in (os.path.dirname(c.socket_path), c.shm_dir):
            assert os.stat(path).st_mode & 0o777 == 0o700, path
        assert os.stat(c.socket_path).st_mode & 0o077 == 0, "socket not accessible to group/other"


# -- 5. API polish ----------------------------------------------------------------------


def test_status_has_exploitability_iteration_and_dtype(tmp_path):
    with make_client(tmp_path) as client:
        job = poll_until(client, submit(client, dict(body(iterations=300, every=100), dtype="float64")))
        assert job["status"] == "done"
        assert job["dtype"] == "float64"
        assert job["exploitability"] is not None
        assert job["exploitability_iteration"] == job["iteration"] == 300
        assert job["exploitability_history"][-1]["iteration"] <= job["exploitability_iteration"]
        default = poll_until(client, submit(client, body()))
        assert default["dtype"] == "float32"
        bad = client.post("/v1/solves", json=dict(body(), dtype="float64", solver_options={"dtype": "float32"}))
        assert bad.status_code == 422
        assert client.post("/v1/solves", json=dict(body(), dtype="float16")).status_code == 422


def test_dtype_field_keeps_old_idempotency_hashes():
    from holdem_solver.api.models import SolveRequest

    req = SolveRequest(**body())
    assert "dtype" not in req.hash_body()


def test_hand_class_and_grid_order():
    assert hand_class("AsKs") == "AKs"
    assert hand_class("KdAc") == "AKo"
    assert hand_class("9d9h") == "99"
    assert hand_class("2c3c") == "32s"
    assert hand_class("fake") is None
    classes = hand_classes()
    assert len(classes) == len(set(classes)) == 169
    assert classes[:3] == ["AA", "AKs", "AQs"] and classes[13] == "AKo" and classes[-1] == "22"


def test_hand_grid_aggregates_combos():
    keys = ["p0|AsKs||", "p0|AhKh|", "p0|AdKc|", "p1|QQ|", "p1|QhQd|check", "p0|AsKs|check,b10"]
    keys[0] = "p0|AsKs|"
    keys[3] = "p1|QsQc|check"
    actions = [["check", "b10"]] * 3 + [["fold", "call"]] * 2 + [["fold", "call"]]
    import numpy as np

    values = np.array([0.2, 0.8, 0.6, 0.4, 1.0, 0.0, 0.3, 0.7, 0.5, 0.5, 0.9, 0.1])
    offsets = np.array([0, 2, 4, 6, 8, 10, 12])
    mapped = map_strategy(keys, actions, offsets, values, 100)
    grid = hand_grid(mapped)
    by = {(n["player"], tuple(n["history"])): n for n in grid["nodes"]}
    root = by[(0, ())]
    assert len(root["grid"]) == 169 and root["combos"] == 3
    cells = {c["hand_class"]: c for c in root["grid"]}
    assert cells["AKs"]["combos"] == 2
    assert cells["AKs"]["frequencies"] == pytest.approx({"check": 0.4, "b10": 0.6})
    assert cells["AKo"]["frequencies"] == pytest.approx({"check": 1.0, "b10": 0.0})
    assert cells["QQ"]["combos"] == 0 and cells["QQ"]["frequencies"] == {}
    qq = {c["hand_class"]: c for c in by[(1, ("check",))]["grid"]}["QQ"]
    assert qq["combos"] == 2 and qq["frequencies"] == pytest.approx({"fold": 0.4, "call": 0.6})
    only = hand_grid(mapped, player=0, history=["check", "b10"])
    assert [(n["player"], n["history"]) for n in only["nodes"]] == [(0, ["check", "b10"])]
    assert [n["history"] for n in hand_grid(mapped, player=0, history=[])["nodes"]] == [[]]


def test_strategy_hand_grid_view_endpoint(tmp_path):
    with make_client(tmp_path) as client:
        job = poll_until(client, submit(client, body()))
        default = client.get(f"/v1/solves/{job['id']}/strategy").json()
        assert default["view"] == "infosets" and default["infosets"]
        grid = client.get(f"/v1/solves/{job['id']}/strategy", params={"view": "hand_grid"})
        assert grid.status_code == 200, grid.text
        assert grid.json()["view"] == "hand_grid"
        assert grid.json()["nodes"] == [], "fake-engine keys are not HUNL infosets"
        assert client.get(f"/v1/solves/{job['id']}/strategy", params={"view": "bogus"}).status_code == 422


def test_openapi_example_per_endpoint(tmp_path):
    with make_client(tmp_path) as client:
        spec = client.get("/openapi.json").json()
    operations = [(path, method, op) for path, item in spec["paths"].items() for method, op in item.items()]
    assert {(p, m) for p, m, _ in operations} >= {
        ("/healthz", "get"), ("/metrics", "get"), ("/v1/gpus", "get"), ("/v1/solves", "post"),
        ("/v1/solves/{job_id}", "get"), ("/v1/solves/{job_id}", "delete"), ("/v1/solves/{job_id}/strategy", "get"),
    }
    for path, method, op in operations:
        ok = next(code for code in op["responses"] if code.startswith("2"))
        content = op["responses"][ok]["content"]
        assert any("example" in media for media in content.values()), (path, method)
    request_schema = spec["components"]["schemas"]["SolveRequest"]
    assert request_schema["examples"][0]["dtype"] == "float32"
    assert "dtype" in request_schema["properties"]
    assert "exploitability_iteration" in spec["components"]["schemas"]["SolveStatus"]["properties"]


# -- 3. external (systemd-managed) workers -------------------------------------------------


class Supervisor:
    """Stands in for systemd ``Restart=always``: respawns the worker when it exits."""

    def __init__(self, runtime, gpu=0):
        self.socket = str(runtime / ("gpu%d.sock" % gpu))
        self.shm = "/dev/shm/holdem_solver_test_%d_%d" % (os.getuid(), os.getpid())
        self.gpu = gpu
        self.pids = []
        self._proc = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _spawn(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "holdem_solver.engine_worker", "--engine", "fake", "--gpu", str(self.gpu),
             "--socket", self.socket, "--shm-dir", self.shm],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.pids.append(self._proc.pid)

    def _loop(self):
        while not self._stop.is_set():
            if self._proc is None or self._proc.poll() is not None:
                time.sleep(0.2)  # RestartSec
                self._spawn()
            time.sleep(0.05)

    def __enter__(self):
        os.makedirs(os.path.dirname(self.socket), mode=0o700, exist_ok=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(10)

    def kwargs(self):
        return {self.gpu: {"socket_path": self.socket, "shm_dir": self.shm, "start_timeout": 20.0}}


EXTERNAL = SchedulerConfig(heartbeat_seconds=0.25, hung_worker_seconds=2.0, worker_mode="external")


def test_external_workers_survive_api_restart_and_crash_recovery(tmp_path):
    runtime = Path("/tmp") / ("hs_ext_%d" % os.getpid())
    with Supervisor(runtime) as sup:
        wait_for(lambda: os.path.exists(sup.socket), message="external worker socket")
        with make_client(tmp_path, config=EXTERNAL, client_kwargs=sup.kwargs()) as client:
            first = sup.pids[-1]
            assert worker_pid(client, 0) == first
            assert poll_until(client, submit(client, body()))["status"] == "done"
        # Stopping the API leaves the externally supervised worker running.
        time.sleep(1.0)
        assert sup.pids == [first] and os.path.exists(sup.socket)
        with make_client(tmp_path, config=EXTERNAL, client_kwargs=sup.kwargs()) as client:
            assert worker_pid(client, 0) == first
            # Worker crash mid-solve: the job fails, the supervisor restarts the
            # worker and the API reattaches on its own.
            job_id = submit(client, slow())
            poll_until(client, job_id, statuses=("solving",))
            os.kill(first, signal.SIGKILL)
            job = poll_until(client, job_id, timeout=30)
            assert job["status"] == "failed" and job["error_code"] == "worker_died", job
            wait_for(lambda: len(sup.pids) >= 2 and worker_pid(client, 0) == sup.pids[-1], message="reattach")
            assert poll_until(client, submit(client, body()))["status"] == "done"
            # Hung worker: the watchdog SIGKILLs the pid; the supervisor restarts it.
            job_id = submit(client, slow())
            poll_until(client, job_id, statuses=("solving",))
            hung = worker_pid(client, 0)
            os.kill(hung, signal.SIGSTOP)
            job = poll_until(client, job_id, timeout=30)
            assert job["status"] == "failed" and job["error_code"] == "worker_hung", job
            wait_for(lambda: worker_pid(client, 0) not in (None, hung), message="new worker after hang")
            assert poll_until(client, submit(client, body()))["status"] == "done"
            assert client.app.state.scheduler.gpu_metrics()[0]["restarts"] >= 1


def test_crash_during_solve_counts_as_restart(tmp_path):
    with make_client(tmp_path) as client:
        job_id = submit(client, slow())
        poll_until(client, job_id, statuses=("solving",))
        os.kill(worker_pid(client, 0), signal.SIGKILL)
        assert poll_until(client, job_id, timeout=30)["error_code"] == "worker_died"
        wait_for(lambda: client.app.state.scheduler.gpu_metrics()[0]["healthy"], message="worker back")
        assert client.app.state.scheduler.gpu_metrics()[0]["restarts"] == 1
        assert _metric(client.get("/metrics").text, "holdem_worker_restarts_total", gpu="0") == 1


def test_missing_external_worker_does_not_block_startup(tmp_path):
    runtime = Path("/tmp") / ("hs_late_%d" % os.getpid())
    sup = Supervisor(runtime)
    t0 = time.monotonic()
    with make_client(tmp_path, config=EXTERNAL, client_kwargs=sup.kwargs()) as client:
        assert time.monotonic() - t0 < 5.0
        # worker missing -> readiness 503 with degraded payload (liveness ok)
        resp = client.get("/healthz")
        assert resp.status_code == 503 and resp.json()["status"] == "degraded"
        assert client.app.state.scheduler.gpu_metrics()[0]["healthy"] is False
        with sup:  # the worker unit comes up after the API
            wait_for(lambda: client.app.state.scheduler.gpu_metrics()[0]["healthy"], timeout=30, message="attached")
            assert poll_until(client, submit(client, body()), timeout=30)["status"] == "done"


def test_store_disk_full_mid_solve_does_not_leave_job_stuck(tmp_path):
    import sqlite3

    with make_client(tmp_path) as client:
        store = client.app.state.scheduler._store
        real_update, real_finish = store.update, store.finish
        state = {"update": 0, "finish": 0}

        def update(job_id, **fields):
            if "iteration" in fields and state["update"] < 1:
                state["update"] += 1
                raise sqlite3.OperationalError("database or disk is full")
            return real_update(job_id, **fields)

        def finish(job_id, status, **fields):
            if state["finish"] < 3:  # still full when the runner records the failure
                state["finish"] += 1
                raise sqlite3.OperationalError("database or disk is full")
            return real_finish(job_id, status, **fields)

        store.update, store.finish = update, finish
        job = poll_until(client, submit(client, slow()), timeout=30)
        assert (job["status"], job["error_code"]) == ("failed", "disk_full"), job
        assert state == {"update": 1, "finish": 3}
        store.update, store.finish = real_update, real_finish
        after = poll_until(client, submit(client, body()))
        assert after["status"] == "done", (after["error_code"], after["error"])
