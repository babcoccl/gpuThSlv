"""End-to-end API test on the real engine; requires a CUDA GPU.

Solves the 573k-node broad river spot (Phase 2's ``river-broad-standard``)
through HTTP and checks exploitability decreases over the run.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402

from test_api import poll_until  # noqa: E402


# river-broad-standard from docs/PHASE2.md: 209x209 hands, 573,031 nodes.
BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo"


def test_river_spot_exploitability_decreases(tmp_path):
    app = create_app(gpus=[0], engine="real", data_dir=str(tmp_path / "api"))
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/solves",
            json={
                "board": "KsJsTh7d2c",
                "range0": BROAD,
                "range1": BROAD,
                "pot": 20.0,
                "stack": 100.0,
                "bet_sizes": "standard",
                "iterations": 400,
                "progress_every": 100,
            },
        )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["id"]
        assert client.get("/v1/gpus").json()[0]["state"] in ("unbuilt", "idle", "solving")

        import time

        deadline = time.time() + 300
        status = {}
        while time.time() < deadline:
            status = client.get(f"/v1/solves/{job_id}").json()
            if status["status"] in ("done", "cancelled", "failed"):
                break
            time.sleep(1.0)
        assert status["status"] == "done", status
        assert status["num_nodes"] == 573031
        history = status["exploitability_history"]
        assert len(history) >= 2
        assert history[-1]["exploitability"] < history[0]["exploitability"]
        assert status["exploitability"] == pytest.approx(history[-1]["exploitability"])
        assert status["measured_vram"]["reserved_bytes"] > 0

        strategy = client.get(f"/v1/solves/{job_id}/strategy").json()
        assert strategy["unmapped_keys"] == 0
        infoset = next(i for i in strategy["infosets"] if i["player"] == 1 and i["history"])
        labels = {a["label"] for a in infoset["actions"]}
        assert labels <= {"fold", "call"} or any(l.startswith("bet ") for l in labels)
