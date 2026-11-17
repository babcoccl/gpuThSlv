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


def test_raises_and_per_player_bet_sizes_over_rest(tmp_path):
    """POST /v1/solves honors raise_cap, raise_sizes, and per-player bet_sizes:
    the built tree's nodes and action sets match SpotConfig semantics directly.

    SpotConfig semantics checked here: bets are ``fraction * pot`` (capped at
    stack); a ``["p", f]`` raise raises to ``bet_level + f * pot_after_call``;
    ``raise_cap`` counts raise actions per street, so at cap 1 a player facing
    a raise sees only fold/call.
    """
    app = create_app(gpus=[0], engine="real", data_dir=str(tmp_path / "api"))
    with TestClient(app) as client:
        accepted = client.post(
            "/v1/solves",
            json={
                "board": ["Ks", "7h", "2d", "5c", "9s"],
                "range0": "AA,KK",
                "range1": "QQ,JJ",
                "pot": 20.0,
                "stack": 100.0,
                "bet_sizes": {"0": [0.33], "1": [0.66]},
                "raise_cap": 1,
                "raise_sizes": [["p", 0.5]],
                "iterations": 20,
            },
        )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["id"]
        status = poll_until(client, job_id, timeout=120.0)
        assert status["status"] == "done", status

        # Expected tree computed from SpotConfig semantics (float %g names).
        pot = 20.0
        b0 = 0.33 * pot                      # player 0's bet: 6.6
        b1 = 0.66 * pot                      # player 1's bet: 13.2
        r1 = b1 + 0.5 * (pot + 2.0 * b1)     # 36.4 raise over b13.2
        r0 = b0 + 0.5 * (pot + 2.0 * b0)     # 23.2 raise over b6.6
        expected = {
            (0, ()): {"check", "b%g" % b0},
            (1, ("check",)): {"check", "b%g" % b1},
            (1, ("b%g" % b0,)): {"fold", "call", "r%g" % r0},
            (0, ("check", "b%g" % b1)): {"fold", "call", "r%g" % r1},
            # raise_cap=1 exhausted: facing a raise leaves fold/call only.
            (0, ("b%g" % b0, "r%g" % r0)): {"fold", "call"},
            (1, ("check", "b%g" % b1, "r%g" % r1)): {"fold", "call"},
        }
        # Per deal: 6 action nodes + 9 terminal nodes; 108 compatible deals
        # (9 unblocked AA/KK combos x 12 QQ/JJ combos share no cards).
        assert status["num_nodes"] == 1 + 108 * 15

        strategy = client.get(f"/v1/solves/{job_id}/strategy").json()
        assert strategy["unmapped_keys"] == 0
        seen = {}
        for infoset in strategy["infosets"]:
            key = (infoset["player"], tuple(infoset["history"]))
            names = frozenset(a["name"] for a in infoset["actions"])
            assert key not in seen or seen[key] == names  # per-combo infosets agree
            seen[key] = names
        assert seen == expected
