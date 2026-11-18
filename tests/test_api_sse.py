"""Runs in ``.venv-api``.

Phase 9.1 SSE contract tests for ``GET /v1/solves/{job}/events``.

Contract (docs/PHASE9_1.md, README):

* the first SSE data frame on every connection — new or reconnecting — is a
  ``snapshot`` event carrying the current job state;
* a ``retry:`` field is sent first; heartbeat comments (``: keep-alive``)
  keep idle streams warm; ``Cache-Control: no-cache`` and
  ``X-Accel-Buffering: no`` disable proxy buffering;
* ``Last-Event-ID`` replay happens only when the id parses as
  ``<boot>-<seq>`` for the current process AND is still inside the replay
  buffer window — then buffered events after it are replayed in order and
  the stream goes live;
* stale (below window), evicted, malformed, future, or pre-restart ids —
  and finished jobs — get snapshot then live events (or the terminal
  ``done`` event), never skipping the snapshot.

Event ids are ``<boot>-<seq>``: the ``boot`` token changes on every API
restart so an old id can never alias a fresh buffer.
"""

import json
import threading

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

# ~4 s solve: enough progress events to fill a small replay buffer.
SLOW_SOLVE = {
    **RIVER_SPOT,
    "iterations": 200,
    "progress_every": 4,
    "solver_options": {"fake_iter_seconds": 0.02},
}
FAST_SSE = {"sse_poll_seconds": 0.02, "sse_heartbeat_seconds": 0.1, "sse_retry_ms": 1500}


def make_client(tmp_path, **kwargs):
    kwargs = {**FAST_SSE, **kwargs}
    app = create_app(data_dir=str(tmp_path / "api"), **kwargs)
    return app, TestClient(app)


def parse_frame(chunk):
    """Chunk -> (id, event, data|None, is_retry, is_comment)."""
    fid = event = None
    data = None
    for line in chunk.split("\n"):
        if line.startswith("id: "):
            fid = line[4:]
        elif line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return fid, event, data, chunk.startswith("retry:"), chunk.startswith(":")


def read_frames(resp_text):
    return [c.strip() for c in resp_text.split("\n\n") if c.strip()]


def stream_frames(client, job_id, headers=None, max_frames=30):
    """Open an SSE stream and yield frames one at a time (up to
    ``max_frames``). The caller controls when to stop reading; the
    generator is abandoned when the inner generator returns."""
    with client.stream(
        "GET", f"/v1/solves/{job_id}/events", headers=headers or {}
    ) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        buf = ""
        n = 0
        for text in resp.iter_text():
            buf += text
            while "\n\n" in buf:
                chunk, buf = buf.split("\n\n", 1)
                if chunk.strip():
                    yield resp, parse_frame(chunk.strip())
                    n += 1
                    if n >= max_frames:
                        return


def collect_frames(client, job_id, headers=None, max_frames=60):
    gen = stream_frames(client, job_id, headers=headers, max_frames=max_frames)
    frames = []
    resp = None
    for r, f in gen:
        resp = r
        frames.append(f)
        if f[1] in ("done", "gone"):
            break
    return resp, frames


def start_job(client, body=None):
    accepted = client.post("/v1/solves", json=body or SLOW_SOLVE)
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["id"]


def wait_done(client, job_id, timeout=30.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.get(f"/v1/solves/{job_id}").json()["status"] == "done":
            return
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never finished")


def seq_of(frame_id):
    return int(frame_id.rpartition("-")[2])


def boot_of(frame_id):
    return frame_id.rpartition("-")[0]


def test_sse_new_client_starts_with_snapshot(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = start_job(client)
        resp, frames = collect_frames(client, job_id)

        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"
        # the first frame is the retry directive (not an event)
        assert frames[0][3] is True

        # snapshot is the first data event and describes current state
        first_event = next(f for f in frames if f[1])
        assert first_event[1] == "snapshot"
        assert "status" in first_event[2] and "iteration" in first_event[2]
        assert boot_of(first_event[0]) and seq_of(first_event[0]) >= 1
        # terminal stream ends with done
        assert frames[-1][1] == "done"
        assert frames[-1][2]["status"] == "done"


def test_sse_reconnect_replays_in_order(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = start_job(client)
        # open the first stream while the job runs; keep a mid-run id
        seen = []
        for _r, f in stream_frames(client, job_id, max_frames=40):
            if f[0] and f[1] in ("snapshot", "progress"):
                seen.append(f)
            if len(seen) >= 4:
                break
        assert len(seen) >= 3
        mid_id = seen[1][0]

        # reconnect mid-buffer: snapshot first, then replayed events after
        # the id, then live events until done
        resp, frames = collect_frames(client, job_id, headers={
            "Last-Event-ID": mid_id,
        })
        events = [f for f in frames if f[1]]
        assert events[0][1] == "snapshot"
        snap_seq = seq_of(events[0][0])
        mid_seq = seq_of(mid_id)
        replayed = [f for f in events[1:] if seq_of(f[0]) < snap_seq]
        live = [f for f in events[1:] if seq_of(f[0]) > snap_seq]
        assert replayed, "expected replayed buffered events"
        assert all(seq_of(f[0]) > mid_seq for f in replayed)
        assert [seq_of(f[0]) for f in replayed] == sorted(
            seq_of(f[0]) for f in replayed
        )
        assert frames[-1][1] == "done"
        assert all(f[1] != "snapshot" for f in live)


def test_sse_stale_and_malformed_and_future_ids(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = start_job(client)
        _, frames = collect_frames(client, job_id)
        boot = boot_of(next(f[0] for f in frames if f[0]))

        bad_ids = [
            "garbage",                      # malformed
            "12345",                        # no boot prefix
            f"{boot}-abc",                  # malformed seq
            "deadbeef-3",                   # other boot (pre-restart style)
            f"{boot}-999999",               # future seq
        ]
        for bad in bad_ids:
            _, frames = collect_frames(
                client, job_id, headers={"Last-Event-ID": bad}
            )
            events = [f for f in frames if f[1]]
            assert events[0][1] == "snapshot", bad
            snap_seq = seq_of(events[0][0])
            # nothing older than the snapshot is replayed
            assert all(seq_of(f[0]) > snap_seq for f in events[1:-1]), bad
            assert frames[-1][1] == "done"


def test_sse_reconnect_at_buffer_edge_still_replays(tmp_path):
    """A Last-Event-ID equal to the OLDEST retained event is still valid:
    the connect-time snapshot append must not evict it before the replay
    window is judged."""
    app, client = make_client(
        tmp_path, gpus=[0], engine="fake", sse_buffer_size=4
    )
    with client:
        job_id = start_job(client)
        # events are only recorded while a stream is open; read until the
        # 4-slot buffer is full, then disconnect so the buffer freezes
        buf = None
        for _r, f in stream_frames(client, job_id, max_frames=40):
            buf = app.state.sse_buffers.get(job_id)
            if buf is not None and len(buf) == 4:
                break
        assert buf is not None and len(buf) == 4
        edge_seq, expected = buf[0][0], [s for s, _p in list(buf)[1:]]

        _, frames = collect_frames(client, job_id, headers={
            "Last-Event-ID": "%s-%d" % (app.state.sse_boot, edge_seq),
        })
        events = [f for f in frames if f[1]]
        assert events[0][1] == "snapshot"
        # the frames right after the snapshot are the replayed buffered
        # events (a deduped snapshot may share the last buffered seq, so
        # compare by position, not by seq < snap_seq)
        replayed = events[1:1 + len(expected)]
        assert [seq_of(f[0]) for f in replayed] == expected
        assert all(f[1] == "progress" for f in replayed)


def test_sse_evicted_id_gets_snapshot_no_replay(tmp_path):
    """Deterministic: the job is a store-driven job on a no-worker app —
    the dispatcher never claims it, and progress events are emitted by
    explicit ``store.update`` calls read in lockstep off the stream. No
    sleeps, no timing assumptions: one update = exactly one buffered seq
    (identical consecutive payloads are deduped)."""
    app, client = make_client(
        tmp_path, gpus=[], engine="fake", sse_buffer_size=4
    )
    with client:
        store = app.state.store
        job, _replayed = store.create(RIVER_SPOT, {"iterations": 10})
        job_id = job["id"]
        assert store.transition(job_id, "solving", ("queued", "building"))

        # Stream the connect snapshot, then drive 6 progress updates: seqs
        # 2..7 land in the 4-slot buffer and evict seqs 1..3.
        stream = stream_frames(client, job_id, max_frames=60)
        snap_id = None
        for _r, f in stream:
            if f[1] == "snapshot":
                snap_id = f[0]
                break
        assert snap_id is not None
        for i in range(1, 7):
            assert store.update(job_id, iteration=i, iterations_run=i)
            progress = None
            for _r, f in stream:
                if f[1] == "progress":
                    progress = f
                    break
            assert progress is not None
            assert progress[2]["iterations_run"] == i
        buf = app.state.sse_buffers[job_id]
        assert seq_of(snap_id) < buf[0][0]  # oldest is evicted, always

        # Reconnect with the evicted id while the job still runs: snapshot
        # then live — the evicted window is not replayed.
        frames = []
        for _r, f in stream_frames(
            client, job_id, headers={"Last-Event-ID": snap_id}, max_frames=10
        ):
            frames.append(f)
            if f[1] == "snapshot":
                assert store.finish(job_id, "done")
        events = [f for f in frames if f[1]]
        assert events[0][1] == "snapshot"
        assert events[0][2]["status"] == "solving"
        snap_seq = seq_of(events[0][0])
        assert all(
            seq_of(f[0]) > snap_seq for f in events[1:] if f[0]
        )  # nothing replayed
        assert events[-1][1] == "done"


def test_sse_cancel_requested_snapshot_reports_cancelled(tmp_path):
    """Deterministic regression for the cancel-vs-connect race: a running
    job that got DELETEd keeps ``status`` in a RUNNING state until the
    runner's finish lands, but a connecting client must already see
    "cancelled" — snapshot then done, never a stale "solving"."""
    app, client = make_client(tmp_path, gpus=[], engine="fake")
    with client:
        store = app.state.store
        job, _ = store.create(RIVER_SPOT, {"iterations": 10})
        job_id = job["id"]
        assert store.transition(job_id, "solving", ("queued", "building"))
        # DELETE on a running job: cancel_requested set, status still
        # "solving" (no worker exists to finish it — the race window is
        # held open).
        resp = client.delete(f"/v1/solves/{job_id}")
        assert resp.status_code == 200
        assert store.get(job_id)["status"] == "solving"
        _, frames = collect_frames(client, job_id)
        events = [f for f in frames if f[1]]
        assert [f[1] for f in events] == ["snapshot", "done"]
        assert events[0][2]["status"] == "cancelled"


def test_sse_finished_and_cancelled_jobs(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        done_id = start_job(
            client,
            {**RIVER_SPOT, "iterations": 10,
             "solver_options": {"fake_iter_seconds": 0.001}},
        )
        wait_done(client, done_id)  # already finished at connect time
        _, frames = collect_frames(client, done_id)
        events = [f for f in frames if f[1]]
        assert [f[1] for f in events] == ["snapshot", "done"]
        assert events[0][2]["status"] == "done"

        cancel_id = start_job(client)
        client.delete(f"/v1/solves/{cancel_id}")
        _, frames = collect_frames(client, cancel_id)
        events = [f for f in frames if f[1]]
        assert events[0][1] == "snapshot" and events[0][2]["status"] == "cancelled"
        assert events[-1][1] == "done" and events[-1][2]["status"] == "cancelled"


def test_sse_restart_ids_never_alias(tmp_path):
    data_dir = str(tmp_path / "api")
    app1 = create_app(gpus=[0], engine="fake", data_dir=data_dir, **FAST_SSE)
    old_ids = []
    with TestClient(app1) as client1:
        job_id = start_job(client1)
        _, frames = collect_frames(client1, job_id)
        old_ids = [f[0] for f in frames if f[0]]
    assert old_ids
    old_boot = boot_of(old_ids[-1])

    # simulate an API restart: same store, new boot token, empty buffer
    app2 = create_app(gpus=[], engine="fake", data_dir=data_dir, **FAST_SSE)
    assert app2.state.sse_boot != old_boot
    with TestClient(app2) as client2:
        assert client2.get(f"/v1/solves/{job_id}").json()["status"] == "done"
        _, frames = collect_frames(
            client2, job_id, headers={"Last-Event-ID": old_ids[-1]}
        )
        events = [f for f in frames if f[1]]
        assert events[0][1] == "snapshot"
        assert [f[1] for f in events] == ["snapshot", "done"]
        assert boot_of(events[0][0]) == app2.state.sse_boot


def test_sse_two_concurrent_clients(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        job_id = start_job(client)
        results = {}

        def watch(name):
            _r, frames = collect_frames(client, job_id)
            results[name] = frames

        threads = [threading.Thread(target=watch, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(results) == 2
        for frames in results.values():
            events = [f for f in frames if f[1]]
            assert events[0][1] == "snapshot"
            assert frames[-1][1] == "done"
            seqs = [seq_of(f[0]) for f in events]
            assert seqs == sorted(seqs)


def test_sse_buffer_overflow(tmp_path):
    _app, client = make_client(
        tmp_path, gpus=[0], engine="fake", sse_buffer_size=4
    )
    with client:
        job_id = start_job(client)
        _, frames = collect_frames(client, job_id)
        events = [f for f in frames if f[1]]
        assert len(events) > 4
        buf = _app.state.sse_buffers[job_id]
        assert len(buf) == 4  # deque maxlen respected
        seqs = [s for s, _ in buf]
        assert seqs == sorted(seqs) and seqs[0] > 1  # oldest evicted


def test_sse_heartbeat_and_404(tmp_path):
    _app, client = make_client(tmp_path, gpus=[0], engine="fake")
    with client:
        assert client.get("/v1/solves/nope/events").status_code == 404

        # an idle queued job emits keep-alive comments between changes
        job_id = client.post("/v1/solves", json={
            **RIVER_SPOT, "iterations": 200,
            "solver_options": {"fake_iter_seconds": 0.05},
        }).json()["id"]
        comments = 0
        for _r, f in stream_frames(client, job_id, max_frames=200):
            if f[4]:
                comments += 1
            if comments >= 1 or (f[1] == "done"):
                break
        assert comments >= 1
        client.delete(f"/v1/solves/{job_id}")
