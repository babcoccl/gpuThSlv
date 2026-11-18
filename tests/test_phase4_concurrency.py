"""Runs in ``.venv-api``.

Phase 4 concurrency + chaos test on two fake-engine GPUs."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402
from holdem_solver.api.scheduler import SchedulerConfig  # noqa: E402

from phase4_concurrency import run_mixed  # noqa: E402
from phase4_util import body  # noqa: E402

RANGES = ["QQ+, AKs", "TT+, AQs+, AKo", "88+, ATs+, KQs, AJo+", "22+, A2s+, KTs+, ATo+"]


def mixed_jobs():
    jobs = []
    for i in range(10):
        spot = {"range0": RANGES[i % 4], "range1": RANGES[(i + 1) % 4]}
        payload = body(iterations=(100, 400, 800, 1500)[i % 4], every=50, priority=i % 3,
                       fake_iter_seconds=0.002 * (1 + i % 4))
        payload.update(spot)
        jobs.append(payload)
    jobs[2]["solver_options"]["fake_oom_at_iteration"] = 200  # one CUDA OOM
    jobs[6]["solver_options"]["fake_fail_solve"] = True  # one engine error
    jobs[8].update(iterations=20000, timeout_seconds=0.5)  # one timeout
    return jobs


def test_ten_mixed_jobs_two_gpus_with_sigkill(tmp_path):
    app = create_app(gpus=[0, 1], engine="fake", data_dir=str(tmp_path / "api"),
                     scheduler_config=SchedulerConfig(heartbeat_seconds=0.25, hung_worker_seconds=3.0))
    with TestClient(app) as client:
        jobs, report = run_mixed(client, mixed_jobs(), chaos_index=3, timeout=120)
    codes = [job["error_code"] for job in jobs]
    assert (codes[2], codes[3], codes[6], codes[8]) == ("cuda_oom", "worker_died", "engine_error", "timeout"), codes
    others = [job for i, job in enumerate(jobs) if i not in (2, 3, 6, 8)]
    assert all(job["status"] == "done" for job in others), [(j["id"], j["status"], j["error"]) for j in others]
    assert set(report["jobs_per_gpu"]) == {0, 1}
    assert sum(report["jobs_per_gpu"].values()) == 10
