"""Runs in ``.venv-api``.

Phase 9 API tests: /tree, /node, /events (SSE), /v1/spots, browser auth.

Runs on ``FakeEngine`` with ``fake_real_keys`` so the strategy export is a
real parseable cap-0 river tree; no GPU needed.
"""

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

REAL_SOLVE = {**RIVER_SPOT, "iterations": 60, "progress_every": 10,
              "solver_options": {"fake_real_keys": True}}

AUTH = {"Authorization": "Bearer secret"}


def make_client(tmp_path, **kwargs):
    app = create_app(data_dir=str(tmp_path / "api"), **kwargs)
    return app, TestClient(app)


def poll_until(client, job_id, statuses=("done", "cancelled", "failed"), timeout=60.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/v1/solves/{job_id}").json()
        if status["status"] in statuses:
            return status
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never finished: {status}")


def solved_job(client):
    accepted = client.post("/v1/solves", json=REAL_SOLVE)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["id"]
    assert poll_until(client, job_id)["status"] == "done"
    return job_id


def test_tree_navigation(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = solved_job(client)

        root = client.get(f"/v1/solves/{job_id}/tree").json()
        assert root["street"] == "river"
        assert root["player"] == 0
        assert root["pot"] == 20.0
        assert root["stacks"] == {"p0": 100.0, "p1": 100.0}
        assert root["board"] == ["Ks", "Js", "Th", "7d", "2c"]
        assert root["is_chance"] is False
        names = [a["name"] for a in root["actions"]]
        assert "check" in names and names[1].startswith("b")
        child = root["children"][0]
        assert child["edge"] == "check" and child["kind"] == "player"
        assert child["history"] == ["check"]
        assert child["player"] == 1
        assert [a["name"] for a in child["actions"]] == names

        node = client.get(f"/v1/solves/{job_id}/tree?history=check,b20").json()
        assert node["player"] == 0
        assert [a["name"] for a in node["actions"]] == ["fold", "call"]
        assert node["children"][0]["kind"] == "terminal"
        assert node["children"][0]["outcome"] == "fold"

        # pipe-separated histories work too; terminal histories 404
        assert client.get(f"/v1/solves/{job_id}/tree?history=check|b20").json() == node
        assert client.get(f"/v1/solves/{job_id}/tree?history=check,b20,fold").status_code == 404
        assert client.get(f"/v1/solves/{job_id}/tree?history=nonsense").status_code == 404
        assert client.get("/v1/solves/nope/tree").status_code == 404


def test_tree_chance_node_turn(tmp_path):
    """Turn spots: the root is a chance node listing the dealt-card options."""
    from holdem_solver.api import nodes as api_nodes
    from holdem_solver.protocol import SpotConfig
    from holdem_solver.protocol.evwalk import TreeIndex

    # Chance at ("check",): no rows but river: children.
    by_node = {(0, ()): {"AsAh": {"check": 1.0}}}
    for dealt in ("river:2c", "river:3d"):
        by_node[(0, ("check", dealt))] = {"AsAh": {"check": 0.5, "b10": 0.5}}
    index = TreeIndex(by_node)
    spot = SpotConfig("KsJsTh7d", "AA", "KK", pot=20.0, stack=100.0, bet_sizes="standard")
    node = api_nodes.tree_node(index, spot.to_dict(), ("check",))
    assert node["is_chance"] is True
    assert node["player"] is None
    assert node["dealt_cards"] == ["3d", "2c"]  # sorted by card id
    kinds = {c["edge"]: c["kind"] for c in node["children"]}
    assert kinds == {"2c": "player", "3d": "player"}


def test_node_values(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = solved_job(client)

        body = client.get(f"/v1/solves/{job_id}/node").json()
        assert body["street"] == "river" and body["player"] == 0
        assert body["actions"]
        assert {row["hand_class"] for row in body["hands"]} == {"AA", "KK", "QQ", "AKs"}
        for row in body["hands"]:
            if not row["frequencies"]:
                continue
            assert abs(sum(row["frequencies"].values()) - 1.0) < 1e-4
            assert 0.0 <= row["equity"] <= 1.0
            assert all(-100.0 <= ev <= 100.0 for ev in row["action_evs"].values())
        assert body["ranges"]["p0"] and body["ranges"]["p1"]
        assert abs(sum(e["share"] for e in body["ranges"]["p0"]) - 1.0) < 1e-6

        combos = client.get(f"/v1/solves/{job_id}/node?combos=true").json()
        first = combos["hands"][0]
        assert len(first["combo"]) == 4  # e.g. AsAh
        assert len(first["hand_class"]) == 2 or first["hand_class"] == "AKs"
        assert combos["ranges"]["p0"][0].get("combo")

        node = client.get(f"/v1/solves/{job_id}/node?history=check,b20").json()
        assert [a["name"] for a in node["actions"]] == ["fold", "call"]
        # terminal histories are rejected before any table work: 422
        assert client.get(f"/v1/solves/{job_id}/node?history=check,b20,fold").status_code == 422


def test_events_sse(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        # slow-enough solve so progress events flow
        accepted = client.post("/v1/solves", json={
            **RIVER_SPOT, "iterations": 150, "progress_every": 5,
            "solver_options": {"fake_iter_seconds": 0.01},
        })
        job_id = accepted.json()["id"]
        resp = client.get(f"/v1/solves/{job_id}/events")
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = resp.text.strip().split("\n\n")
        assert frames[0].startswith("retry:")  # retry directive first
        snap = next(f for f in frames if f.startswith("id:"))
        assert "event: snapshot" in snap and snap.startswith("id: ")
        last = frames[-1]
        assert "event: done" in last and '"status": "cancelled"' not in last
        ids = [f.split("\n")[0].split(": ")[1] for f in frames if f.startswith("id:")]
        seqs = [int(i.rpartition("-")[2]) for i in ids]
        assert seqs == sorted(seqs)

        # Reconnect with Last-Event-ID replays buffered events, no duplicates.
        last_id = ids[len(ids) // 2]
        last_seq = int(last_id.rpartition("-")[2])
        resp2 = client.get(
            f"/v1/solves/{job_id}/events", headers={"Last-Event-ID": last_id}
        )
        replayed = [
            int(f.split("\n")[0].split(": ")[1].rpartition("-")[2])
            for f in resp2.text.strip().split("\n\n") if f.startswith("id:")
        ]
        assert all(seq > last_seq for seq in replayed[:-1] if replayed)

        client.delete(f"/v1/solves/{job_id}")  # may already be done
        assert client.get("/v1/solves/nope/events").status_code == 404


def test_spots_crud_and_restart(tmp_path):
    data_dir = str(tmp_path / "api")
    app = create_app(gpus=[0], engine="fake", data_dir=data_dir)
    with TestClient(app) as client:
        created = client.post("/v1/spots", json={
            "name": "dry-river", "spot": RIVER_SPOT, "tags": ["river", "dry"],
            "notes": "baseline",
        })
        assert created.status_code == 201, created.text
        spot_id = created.json()["id"]

        assert client.get(f"/v1/spots/{spot_id}").json()["name"] == "dry-river"
        assert [s["name"] for s in client.get("/v1/spots?tag=dry").json()] == ["dry-river"]
        assert client.get("/v1/spots?tag=wet").json() == []

        patched = client.patch(f"/v1/spots/{spot_id}", json={"notes": "updated"})
        assert patched.json()["notes"] == "updated"

        dup = client.post(f"/v1/spots/{spot_id}/duplicate")
        assert dup.status_code == 201
        assert dup.json()["name"] == "dry-river (copy)"
        # the response model normalizes the stored spot (defaults filled in);
        # every submitted field must round-trip unchanged
        assert RIVER_SPOT.items() <= dup.json()["spot"].items()

        # solving a saved spot queues a real job
        accepted = client.post(f"/v1/spots/{spot_id}/solve", json={"iterations": 20})
        assert accepted.status_code == 202, accepted.text
        poll_until(client, accepted.json()["id"])

        # validation + errors
        assert client.post("/v1/spots", json={"name": "x", "spot": {"board": "As"}}).status_code == 422
        assert client.get("/v1/spots/nope").status_code == 404
        assert client.post("/v1/spots/nope/solve", json={"iterations": 1}).status_code == 404

    # restart: same data_dir, spots survive
    app2 = create_app(gpus=[], engine="fake", data_dir=data_dir)
    with TestClient(app2) as client2:
        names = [s["name"] for s in client2.get("/v1/spots").json()]
        assert "dry-river" in names and "dry-river (copy)" in names

    # storage limit
    app3 = create_app(gpus=[], engine="fake", data_dir=str(tmp_path / "api3"), max_spots=1)
    with TestClient(app3) as client3:
        assert client3.post("/v1/spots", json={"name": "a", "spot": RIVER_SPOT}).status_code == 201
        r = client3.post("/v1/spots", json={"name": "b", "spot": RIVER_SPOT})
        assert r.status_code == 429
        assert client3.delete(f"/v1/spots/{spot_id}").status_code == 404  # different store


def test_spot_delete(tmp_path):
    _app, client = make_client(tmp_path, gpus=[], engine="fake")
    with client:
        spot_id = client.post("/v1/spots", json={"name": "x", "spot": RIVER_SPOT}).json()["id"]
        assert client.delete(f"/v1/spots/{spot_id}").json() == {"status": "deleted"}
        assert client.get(f"/v1/spots/{spot_id}").status_code == 404


def test_pin_retention(tmp_path):
    app = create_app(gpus=[0], engine="fake", data_dir=str(tmp_path / "api"))
    with TestClient(app) as client:
        job_id = solved_job(client)
        assert client.post(f"/v1/solves/{job_id}/pin").json() == {"id": job_id, "pinned": True}
        # pinned jobs are skipped by retention even far in the future
        assert app.state.store.expired_strategies(4e9) == []
        assert client.post(f"/v1/solves/{job_id}/unpin").json()["pinned"] is False
        assert app.state.store.expired_strategies(4e9) != []
        assert client.post("/v1/solves/nope/pin").status_code == 404


def test_browser_auth(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake", api_key="secret")
    with client:
        assert client.post("/v1/auth/login", json={"key": "wrong"}).status_code == 401
        resp = client.post("/v1/auth/login", json={"key": "secret"})
        assert resp.status_code == 200, resp.text
        csrf = resp.json()["csrf"]
        set_cookie = resp.headers["set-cookie"]
        assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie
        assert "holdem_session=" in set_cookie and "holdem_csrf=" in set_cookie

        # cookie auth works for reads; mutating requests need the CSRF token
        assert client.get("/v1/gpus").status_code == 200
        r = client.post("/v1/spots", json={"name": "x", "spot": RIVER_SPOT})
        assert r.status_code == 403
        r = client.post(
            "/v1/spots", json={"name": "x", "spot": RIVER_SPOT},
            headers={"x-csrf-token": csrf},
        )
        assert r.status_code == 201, r.text

        # API key still works for scripts (no CSRF needed)
        r = client.post("/v1/spots", json={"name": "y", "spot": RIVER_SPOT}, headers=AUTH)
        assert r.status_code == 201

        resp = client.post("/v1/auth/logout")
        assert resp.status_code == 200
        assert 'holdem_session="";' in resp.headers["set-cookie"]
        # cookie cleared client-side: the next request carries no session
        assert client.post("/v1/auth/logout").status_code == 401
        assert client.get("/v1/gpus").status_code == 401


def test_session_survives_restart(tmp_path):
    """Session cookies are stateless HMAC tokens: they survive an API
    restart as long as the signing material (HOLDEM_SESSION_SECRET or the
    API key) is unchanged. The login credential is the API key."""
    data_dir = str(tmp_path / "api")
    app1 = create_app(gpus=[0], engine="fake", data_dir=data_dir, api_key="secret")
    with TestClient(app1) as client1:
        resp = client1.post("/v1/auth/login", json={"key": "secret"})
        assert resp.status_code == 200
        session_cookie = client1.cookies.get("holdem_session")
        csrf = resp.json()["csrf"]
        assert session_cookie and session_cookie.startswith("v1.")

    # restart: new process, same key -> same derived signing secret
    app2 = create_app(gpus=[0], engine="fake", data_dir=data_dir, api_key="secret")
    with TestClient(app2) as client2:
        client2.cookies.set("holdem_session", session_cookie)
        client2.cookies.set("holdem_csrf", csrf)  # double-submit pair
        assert client2.get("/v1/gpus").status_code == 200
        r = client2.post(
            "/v1/spots", json={"name": "x", "spot": RIVER_SPOT},
            headers={"x-csrf-token": csrf},
        )
        assert r.status_code == 201, r.text

    # a restart with different signing material invalidates the session
    app3 = create_app(
        gpus=[0], engine="fake", data_dir=data_dir, api_key="secret",
        session_secret="rotated",
    )
    with TestClient(app3) as client3:
        client3.cookies.set("holdem_session", session_cookie)
        assert client3.get("/v1/gpus").status_code == 401


def test_session_ttl_seconds_env(monkeypatch, tmp_path):
    """HOLDEM_SESSION_TTL_SECONDS sets the session cookie lifetime —
    both the Set-Cookie max_age and the signed v1.<expiry> field.
    HOLDEM_SESSION_SECONDS remains an accepted fallback."""
    import time

    for var, ttl in (("HOLDEM_SESSION_TTL_SECONDS", 60),
                     ("HOLDEM_SESSION_SECONDS", 120)):
        monkeypatch.setenv(var, str(ttl))
        other = "HOLDEM_SESSION_SECONDS" if var == "HOLDEM_SESSION_TTL_SECONDS" \
            else "HOLDEM_SESSION_TTL_SECONDS"
        monkeypatch.delenv(other, raising=False)
        app = create_app(gpus=[0], engine="fake", data_dir=str(tmp_path / "api"),
                         api_key="secret")
        with TestClient(app) as client:
            resp = client.post("/v1/auth/login", json={"key": "secret"})
            assert resp.status_code == 200
            set_cookie = resp.headers["set-cookie"]
            assert "Max-Age=%d" % ttl in set_cookie
            exp = int(client.cookies.get("holdem_session").split(".")[1])
            assert abs(exp - (time.time() + ttl)) < 5


def test_login_rate_limit(tmp_path):
    _app, client = make_client(
        tmp_path, gpus=[0], engine="fake", api_key="secret",
        login_rate_per_minute=5.0, login_burst=2,
    )
    with client:
        statuses = [
            client.post("/v1/auth/login", json={"key": "nope"}).status_code
            for _ in range(4)
        ]
        assert 429 in statuses
        assert statuses.count(429) >= 2


def test_cors_disabled_by_default(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        resp = client.get("/healthz", headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in resp.headers


def test_cors_configured(tmp_path):
    _app, client = make_client(
        tmp_path, gpus=[0], engine="fake", cors_origins=["https://example.com"]
    )
    with client:
        resp = client.options(
            "/v1/solves", headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "https://example.com"


def _evs_file(tmp_path, job_id):
    return tmp_path / "api" / f"strategy-{job_id}.evs.npz"


def _live_node(client, job_id):
    body = client.get(f"/v1/solves/{job_id}/node")
    assert body.status_code == 200, body.text
    return body.json()


@pytest.mark.parametrize("kind", ("missing", "corrupt", "stale", "v1"))
def test_node_declined_file_not_rewritten(tmp_path, kind):
    """A declined evs.npz (missing, pre-checksum/format, corrupt, stale)
    is served from a live-computed in-memory table — the request path
    never writes the file back."""
    import numpy as np

    from holdem_solver.api import nodes as api_nodes

    # First app instance finishes the job (writes strategy + evs.npz);
    # the second instance re-opens the same data dir with fresh caches.
    _app0, client0 = make_client(tmp_path, gpus=[0], engine="fake")
    with client0:
        job_id = solved_job(client0)
        live = _live_node(client0, job_id)
    evs_path = _evs_file(tmp_path, job_id)
    strat = tmp_path / "api" / f"strategy-{job_id}.npz"
    keys = api_nodes.load_index(str(strat))[0].keys_list
    assert evs_path.exists()  # written at job completion

    def make_missing(p):
        p.unlink()

    def make_corrupt(p):
        p.write_bytes(b"junk")

    def make_stale(p):
        data = dict(np.load(p).items())
        data["keys"] = np.array([b"stale-key"] * len(keys), dtype=np.bytes_)
        np.savez(p, **data)

    def make_v1(p):
        import zlib

        data = dict(np.load(p).items())
        ev, mass = data["ev"], data["opp_mass"]
        data.pop("format")
        data["crc32"] = np.int64(
            zlib.crc32(ev.tobytes()) ^ zlib.crc32(mass.tobytes())
        )
        np.savez(p, **data)

    mutators = {
        "missing": make_missing,
        "corrupt": make_corrupt,
        "stale": make_stale,
        "v1": make_v1,
    }
    mutators[kind](evs_path)
    assert api_nodes.load_node_evs(str(evs_path), keys) is None
    after = None if not evs_path.exists() else evs_path.read_bytes()

    calls = {"n": 0}
    orig = api_nodes.export_node_evs

    def counted(index, spot):
        calls["n"] += 1
        return orig(index, spot)

    api_nodes.export_node_evs = counted

    def no_write(*a, **kw):
        raise AssertionError("write_node_evs called from the request path")

    orig_write = api_nodes.write_node_evs
    api_nodes.write_node_evs = no_write
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    try:
        with client:
            body = _live_node(client, job_id)
            assert body["hands"] == live["hands"]
            # a second request serves the cached in-memory table
            assert _live_node(client, job_id)["hands"] == live["hands"]
            assert calls["n"] == 1  # one in-memory build, reused
    finally:
        api_nodes.export_node_evs = orig
        api_nodes.write_node_evs = orig_write
    # nothing was written: missing stays missing, declined bytes untouched
    if after is None:
        assert not evs_path.exists()
    else:
        assert evs_path.read_bytes() == after


def test_node_first_requests_single_flight_in_memory(tmp_path):
    """N concurrent first /node requests after a declined file produce
    exactly one table build and zero file writes."""
    import threading

    from holdem_solver.api import nodes as api_nodes

    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    calls = {"n": 0}
    writes = {"n": 0}
    orig = api_nodes.export_node_evs
    orig_write = api_nodes.write_node_evs

    def counted(index, spot):
        calls["n"] += 1
        import time
        time.sleep(0.3)  # widen the race window
        return orig(index, spot)

    def counted_write(*a, **kw):
        writes["n"] += 1
        return orig_write(*a, **kw)

    with client:
        job_id = solved_job(client)
        evs_path = _evs_file(tmp_path, job_id)
        evs_path.unlink()
        api_nodes.export_node_evs = counted
        api_nodes.write_node_evs = counted_write
        try:
            results = []

            def hit():
                results.append(client.get(f"/v1/solves/{job_id}/node").status_code)

            threads = [threading.Thread(target=hit) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            api_nodes.export_node_evs = orig
            api_nodes.write_node_evs = orig_write
        assert results == [200] * 8
        assert calls["n"] == 1, calls
        assert writes["n"] == 0, writes
        assert not evs_path.exists()


def test_node_chance_terminal_no_table_build(tmp_path):
    """Chance and terminal histories 422 BEFORE any table work — zero
    builds even when the job has no usable evs file."""
    import time

    import numpy as np

    from holdem_solver.api import nodes as api_nodes
    from holdem_solver.api.store import JobStore
    from holdem_solver.protocol import SpotConfig

    # Hand-built turn-board strategy: ("check", "check") is a chance node
    # (its children carry "river:" elements); ("b2", "fold") is terminal.
    spot = SpotConfig(
        "KsJsTh7d", "AdAc,QcQh", "AdAc", pot=20.0, stack=100.0,
        bet_sizes="standard",
    ).to_dict()
    keys = [
        "p0|AdAc|", "p0|QcQh|",
        "p1|AdAc|check",
        "p0|AdAc|check|check|river:2c", "p0|AdAc|check|check|river:7h",
        "p1|AdAc|b2",
    ]
    acts = [["check", "b2"], ["check", "b2"], ["check", "b2"],
            ["check", "b2"], ["check", "b2"], ["fold", "call"]]
    offsets = [0]
    values = []
    for names in acts:
        values.extend([0.5] * len(names))
        offsets.append(len(values))
    data_dir = tmp_path / "api"
    data_dir.mkdir()
    store = JobStore(str(data_dir / "solves.db"))
    job, _ = store.create(spot, {})
    job_id = job["id"]
    strat_path = data_dir / f"strategy-{job_id}.npz"
    np.savez(
        strat_path,
        keys=np.array([k.encode() for k in keys], dtype=np.bytes_),
        actions=np.array([",".join(a).encode() for a in acts], dtype=np.bytes_),
        offsets=np.array(offsets, dtype=np.int64),
        values=np.array(values, dtype=np.float64),
        iteration=np.int64(1),
    )
    store.set_fields(
        job_id, status="done", strategy_path=str(strat_path),
        finished_at=time.time(),
    )
    store.close()
    assert not _evs_file(tmp_path, job_id).exists()  # no usable file

    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    calls = {"n": 0}

    def counted(index, spot_):
        calls["n"] += 1
        return orig(index, spot_)

    orig = api_nodes.export_node_evs
    with client:
        api_nodes.export_node_evs = counted
        try:
            assert client.get(
                f"/v1/solves/{job_id}/node?history=check,check"
            ).status_code == 422
            assert client.get(
                f"/v1/solves/{job_id}/node?history=b2,fold"
            ).status_code == 422
        finally:
            api_nodes.export_node_evs = orig
        assert calls["n"] == 0, calls


def test_node_cache_hit_no_table_build_readonly_dir(tmp_path):
    """A payload-cache hit performs zero table builds even when the data
    directory is read-only."""
    import os
    import stat

    from holdem_solver.api import nodes as api_nodes

    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    calls = {"n": 0}
    orig = api_nodes.export_node_evs

    def counted(index, spot):
        calls["n"] += 1
        return orig(index, spot)

    with client:
        job_id = solved_job(client)
        first = _live_node(client, job_id)  # warms payload + table caches
        data_dir = tmp_path / "api"
        os.chmod(data_dir, stat.S_IRUSR | stat.S_IXUSR)  # read-only
        api_nodes.export_node_evs = counted
        try:
            assert _live_node(client, job_id) == first
            assert calls["n"] == 0
        finally:
            api_nodes.export_node_evs = orig
            os.chmod(data_dir, stat.S_IRWXU)
