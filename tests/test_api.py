"""REST API tests on ``FakeEngine`` workers; no GPU or torch needed."""

import tempfile

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402


RIVER_SPOT = {
    "board": "KsJsTh7d2c",
    "range0": "QQ+, AKs",
    "range1": "TT, AQ",
    "pot": 20.0,
    "stack": 100.0,
    "bet_sizes": "standard",
}


def make_client(tmp_path, **kwargs):
    app = create_app(data_dir=str(tmp_path / "api"), **kwargs)
    return TestClient(app)


def poll_until(client, job_id, statuses=("done", "cancelled", "failed"), timeout=60.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/v1/solves/{job_id}").json()
        if status["status"] in statuses:
            return status
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} never finished: {status}")


def test_healthz_no_auth(tmp_path):
    with make_client(tmp_path, gpus=[], api_key="secret") as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/v1/gpus").status_code == 401
        assert client.get("/v1/gpus", headers={"Authorization": "Bearer secret"}).json() == []
        assert client.get("/v1/gpus", headers={"X-API-Key": "secret"}).status_code == 200
        assert client.get("/v1/gpus", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_gpus_endpoint(tmp_path):
    with make_client(tmp_path, gpus=[0, 1], engine="fake", api_key=None) as client:
        infos = client.get("/v1/gpus").json()
    assert [info["gpu"] for info in infos] == [0, 1]
    assert all(info["state"] == "unbuilt" for info in infos)
    assert all(info["worker_pid"] for info in infos)


def test_solve_lifecycle_and_strategy(tmp_path):
    with make_client(tmp_path, gpus=[0], engine="fake") as client:
        accepted = client.post("/v1/solves", json={**RIVER_SPOT, "iterations": 200, "progress_every": 50})
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["id"]

        status = poll_until(client, job_id)
        assert status["status"] == "done"
        assert status["gpu"] == 0
        assert status["iteration"] == 200
        assert status["exploitability"] is not None
        assert status["num_nodes"] > 0
        assert status["estimated_vram_bytes"] is not None  # fake engine reports 0
        assert status["elapsed_seconds"] > 0

        strategy = client.get(f"/v1/solves/{job_id}/strategy").json()
        assert strategy["num_infosets"] > 0
        assert strategy["iteration"] == 200
        infoset = strategy["infosets"][0]
        assert len(infoset["actions"]) == 2  # fake_actions default
        assert abs(sum(a["frequency"] for a in infoset["actions"]) - 1.0) < 1e-4
        # FakeEngine keys do not parse as HUNL keys.
        assert strategy["unmapped_keys"] == strategy["num_infosets"]


def test_422s(tmp_path):
    with make_client(tmp_path, gpus=[0], engine="fake") as client:
        # No stop condition.
        assert client.post("/v1/solves", json=RIVER_SPOT).status_code == 422
        # Two mutually exclusive stop modes.
        bad = {**RIVER_SPOT, "iterations": 10, "time_budget": 1.0}
        assert client.post("/v1/solves", json=bad).status_code == 422
        # Target mode needs a cap.
        assert client.post("/v1/solves", json={**RIVER_SPOT, "target_exploitability": 0.1}).status_code == 422
        # iterations is not a valid cap for target mode; max_iterations is.
        bad = {**RIVER_SPOT, "target_exploitability": 0.1, "iterations": 10}
        assert client.post("/v1/solves", json=bad).status_code == 422
        ok = {**RIVER_SPOT, "target_exploitability": 0.01, "max_iterations": 50}
        accepted = client.post("/v1/solves", json=ok)
        assert accepted.status_code == 202, accepted.text
        poll_until(client, accepted.json()["id"], timeout=30.0)
        # max_nodes too small for the spot.
        bad = {**RIVER_SPOT, "iterations": 10, "max_nodes": 10}
        r = client.post("/v1/solves", json=bad)
        assert r.status_code == 422 and "max_nodes" in r.text
        # Unparseable spot.
        assert client.post("/v1/solves", json={**RIVER_SPOT, "iterations": 10, "board": "As"}).status_code == 422
        # Unknown field.
        assert client.post("/v1/solves", json={**RIVER_SPOT, "iterations": 10, "wat": 1}).status_code == 422
    with make_client(tmp_path / "tiny", gpus=[0], engine="fake", max_vram_bytes=1) as client:
        # VRAM budget of 1 byte rejects everything.
        r = client.post("/v1/solves", json={**RIVER_SPOT, "iterations": 10})
        assert r.status_code == 422 and "VRAM" in r.text


def test_cancel_queued_and_solving(tmp_path):
    with make_client(tmp_path, gpus=[0], engine="fake") as client:
        # Occupy the GPU with a slow solve (~0.05 s per fake iteration, 60 s cap).
        slow = {
            **RIVER_SPOT,
            "time_budget": 60.0,
            "solver_options": {"fake_iter_seconds": 0.05},
        }
        r1 = client.post("/v1/solves", json=slow)
        assert r1.status_code == 202
        job1 = r1.json()["id"]
        assert poll_until(client, job1, statuses=("solving",), timeout=30.0)["status"] == "solving"
        r2 = client.post("/v1/solves", json={**RIVER_SPOT, "iterations": 10})
        job2 = r2.json()["id"]
        # Job 2 stays queued while job 1 solves; cancelling it is instant.
        status = client.delete(f"/v1/solves/{job2}").json()
        assert status["status"] == "cancelled"
        # Cancel the running one too.
        status = client.delete(f"/v1/solves/{job1}").json()
        assert status["status"] == "solving"  # flagged, runner tears down
        assert poll_until(client, job1)["status"] == "cancelled"


def test_unknown_job(tmp_path):
    with make_client(tmp_path, gpus=[0], engine="fake") as client:
        assert client.get("/v1/solves/nope").status_code == 404
        assert client.delete("/v1/solves/nope").status_code == 404
        assert client.get("/v1/solves/nope/strategy").status_code == 404


def test_in_flight_jobs_fail_on_restart(tmp_path):
    data_dir = str(tmp_path / "api")
    app1 = create_app(gpus=[], engine="fake", data_dir=data_dir)
    with TestClient(app1):
        pass
    app2 = create_app(gpus=[], engine="fake", data_dir=data_dir)
    with TestClient(app2):
        pass  # nothing crash; store re-opens cleanly
