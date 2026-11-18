"""Runs in ``.venv-api``.

Shared helpers for the Phase 4 failure-mode tests (fake engine)."""

import time

RIVER_SPOT = {
    "board": "KsJsTh7d2c",
    "range0": "QQ+, AKs",
    "range1": "TT, AQ",
    "pot": 20.0,
    "stack": 100.0,
    "bet_sizes": "standard",
}

TERMINAL = ("done", "cancelled", "failed")


def body(iterations=200, every=50, priority=0, timeout=None, **fake):
    payload = {**RIVER_SPOT, "iterations": iterations, "progress_every": every, "priority": priority}
    if timeout is not None:
        payload["timeout_seconds"] = timeout
    if fake:
        payload["solver_options"] = fake
    return payload


def slow(iterations=100000, iter_seconds=0.005, **kw):
    return body(iterations=iterations, every=50, fake_iter_seconds=iter_seconds, **kw)


def poll_until(client, job_id, statuses=TERMINAL, timeout=60.0):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f"/v1/solves/{job_id}").json()
        if status["status"] in statuses:
            return status
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {statuses}: {status}")


def worker_pid(client, gpu):
    for info in client.get("/v1/gpus").json():
        if info["gpu"] == gpu:
            return info["worker_pid"]
    raise KeyError(gpu)


def wait_for(predicate, timeout=30.0, interval=0.1, message="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {message}")
