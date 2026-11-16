"""EngineClient against a FakeEngine worker process: no GPU, no gpu_cfr."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import pytest

from holdem_solver import protocol
from holdem_solver.client import (
    EngineBusyError,
    EngineClient,
    EngineTimeoutError,
    NotBuiltError,
    RemoteEngineError,
    Strategy,
    WorkerDiedError,
    WorkerStartError,
)
from holdem_solver.client.process import REPO_ROOT


@pytest.fixture
def dirs():
    # Short paths: a Unix socket path is limited to 107 bytes.
    runtime = tempfile.mkdtemp(prefix="hs_rt_")
    shm = tempfile.mkdtemp(prefix="holdem_solver_test_", dir=protocol.SHM_ROOT)
    yield {"socket_path": os.path.join(runtime, "w.sock"), "shm_dir": shm}
    shutil.rmtree(runtime, ignore_errors=True)
    shutil.rmtree(shm, ignore_errors=True)


@pytest.fixture
def client(dirs):
    with EngineClient(None, engine="fake", **dirs) as engine:
        yield engine


def _wait_gone(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_round_trip(client):
    info = client.ping()
    assert info["engine"] == "fake" and info["pid"] == client.worker_pid and info["state"] == "unbuilt"

    built = client.build("kuhn_poker", variant="cfr_plus", seed=42)
    assert (built.game, built.num_nodes, built.num_infosets, built.num_infoset_actions) == ("kuhn_poker", 55, 12, 24)

    result = client.solve(iterations=400, progress_every=100)
    assert result.iterations_run == result.total_iterations == 400
    assert result.cancelled is False
    assert result.exploitability == pytest.approx(0.5 / 401**0.5)

    progress = client.progress()
    assert (progress.state, progress.iteration, progress.target_iterations) == ("done", 400, 400)
    assert progress.exploitability_iteration == 400

    strategy = client.get_strategy()
    assert len(strategy) == 12 and all(key.startswith("fake/") for key in strategy)
    for row in strategy.values():
        assert row.dtype == np.float32 and row.shape == (2,)
        assert row.sum() == pytest.approx(1.0, abs=1e-6)
    # The client consumes the transfer files.
    assert os.listdir(client.shm_dir) == []

    client.reset()
    assert client.progress().iteration == 0
    client.close()
    assert client.ping()["built"] is False
    with pytest.raises(NotBuiltError):
        client.solve(iterations=1)


def test_bulk_strategy_travels_through_shm(client):
    client.build("fake", fake_infosets=20000, fake_actions=5)
    client.solve(iterations=10)
    strategy = client.get_strategy(flat=True)
    assert isinstance(strategy, Strategy)
    assert len(strategy.keys) == 20000 and strategy.values.shape == (100000,)
    assert strategy.offsets[-1] == 100000 and strategy.iteration == 10
    np.testing.assert_allclose(strategy.values.reshape(20000, 5).sum(axis=1), 1.0, rtol=1e-5)
    assert strategy.as_dict()["fake/19999"].shape == (5,)
    assert os.listdir(client.shm_dir) == []


def test_progress_and_cancel_while_solving(client):
    client.build("kuhn_poker", fake_iter_seconds=0.001)
    assert client.solve(iterations=10**7, chunk_iterations=5, wait=False) is None
    with pytest.raises(EngineBusyError):
        client.get_strategy()
    with pytest.raises(EngineBusyError):
        client.build("kuhn_poker")
    with pytest.raises(EngineTimeoutError):
        client.wait(timeout=0.2)
    first = client.progress()
    time.sleep(0.1)
    second = client.progress()
    assert first.state == second.state == "solving"
    assert 0 < first.iteration < second.iteration
    assert client.cancel() is True
    result = client.wait(timeout=10)
    assert result.cancelled is True and 0 < result.iterations_run < 10**7
    assert client.progress().state == "cancelled"
    assert client.cancel() is False


def test_cancel_from_another_thread_unblocks_solve(client):
    client.build("kuhn_poker", fake_iter_seconds=0.001)
    seen = []
    timer = threading.Timer(0.3, client.cancel)
    timer.start()
    try:
        result = client.solve(target_exploitability=1e-9, chunk_iterations=5, on_progress=seen.append)
    finally:
        timer.cancel()
    assert result.cancelled is True and result.target_reached is False


def test_time_budget_solve(client):
    client.build("kuhn_poker", fake_iter_seconds=0.001)
    result = client.solve(time_budget=0.2, chunk_iterations=5)
    assert result.time_budget_exhausted is True and result.iterations_run > 0


def test_remote_errors(client):
    with pytest.raises(RemoteEngineError) as excinfo:
        client.build("no_such_game")
    assert excinfo.value.code == protocol.ERROR_INVALID_PARAMS and excinfo.value.remote_type == "ValueError"

    client.build("kuhn_poker", fake_fail_solve=True)
    with pytest.raises(RemoteEngineError) as excinfo:
        client.solve(iterations=5)
    assert excinfo.value.code == protocol.ERROR_ENGINE
    assert excinfo.value.remote_type == "RuntimeError"
    assert "fake solve failure" in excinfo.value.remote_traceback
    assert client.progress().state == "failed"
    # The worker survives an engine error.
    client.build("kuhn_poker")
    assert client.solve(iterations=5).iterations_run == 5


def test_killed_worker_restarts_cleanly(client, dirs):
    client.build("fake", fake_infosets=100)
    client.solve(iterations=50)
    # Leave an uncollected strategy behind so the restart has something to clean.
    client._call("get_strategy")
    assert len(os.listdir(client.shm_dir)) == 3
    old_pid = client.worker_pid

    os.kill(old_pid, signal.SIGKILL)
    assert client._worker.wait_exit(5.0)
    assert os.path.exists(dirs["socket_path"])  # stale socket left by SIGKILL
    assert client.is_alive() is False
    with pytest.raises(WorkerDiedError):
        client.progress()

    info = client.restart()
    assert info["pid"] == client.worker_pid != old_pid
    assert info["state"] == "unbuilt"
    assert os.listdir(client.shm_dir) == []
    with pytest.raises(NotBuiltError):
        client.solve(iterations=1)
    client.build("kuhn_poker")
    assert client.solve(iterations=20).total_iterations == 20
    assert len(client.get_strategy()) == 12


def test_killed_mid_solve_then_auto_restart(dirs):
    with EngineClient(None, engine="fake", auto_restart=True, **dirs) as client:
        client.build("kuhn_poker", fake_iter_seconds=0.001)
        client.solve(iterations=10**7, chunk_iterations=5, wait=False)
        old_pid = client.worker_pid
        threading.Timer(0.2, os.kill, (old_pid, signal.SIGKILL)).start()
        with pytest.raises(WorkerDiedError, match="fresh worker was started"):
            client.wait(timeout=30)
        assert client.worker_pid != old_pid and client.is_alive()
        assert client.progress().state == "unbuilt"
        client.build("kuhn_poker")
        assert client.solve(iterations=10).iterations_run == 10


def test_shutdown_removes_socket_and_shm_files(dirs):
    client = EngineClient(None, engine="fake", **dirs)
    pid = client.worker_pid
    client.build("kuhn_poker")
    client._call("get_strategy")
    client.shutdown()
    assert _wait_gone(pid)
    assert not os.path.exists(dirs["socket_path"])
    assert os.listdir(dirs["shm_dir"]) == []
    with pytest.raises(WorkerDiedError):
        client.ping()
    # A shut-down client can be started again.
    assert client.start()["pid"] != pid
    client.shutdown()


def test_second_worker_on_same_socket_is_refused(client, dirs):
    with pytest.raises(WorkerStartError, match="already owns|not the worker just spawned"):
        EngineClient(None, engine="fake", start_timeout=10, **dirs)
    assert client.ping()["pid"] == client.worker_pid


def test_attach_to_running_worker(client, dirs):
    attached = EngineClient(None, spawn=False, **dirs)
    assert attached.worker_pid is None and attached.is_alive()
    attached.build("kuhn_poker")
    assert client.solve(iterations=5).iterations_run == 5
    attached.shutdown()  # no-op: not the owner
    assert client.is_alive()


def test_worker_start_failure_reports_log(dirs):
    with pytest.raises(WorkerStartError, match="exited with code"):
        EngineClient(None, engine="fake", python="/bin/false", start_timeout=10, **dirs)
    with pytest.raises(WorkerStartError, match="cannot start worker"):
        EngineClient(None, engine="fake", python="/nonexistent/python", **dirs)


def test_worker_exits_when_client_process_dies(dirs):
    code = (
        "import os, sys\n"
        "from holdem_solver.client import EngineClient\n"
        "c = EngineClient(None, engine='fake', socket_path=sys.argv[1], shm_dir=sys.argv[2])\n"
        "print(c.worker_pid, flush=True)\n"
        "os._exit(0)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, dirs["socket_path"], dirs["shm_dir"]],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert _wait_gone(int(out.stdout.strip()), timeout=10)
    assert not os.path.exists(dirs["socket_path"])


def test_client_import_stays_out_of_the_engine_runtime():
    code = (
        "import sys\n"
        "import holdem_solver.client\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('torch', 'gpu_cfr')"
        " or m.startswith(('holdem_solver.engine', 'holdem_solver.engine_worker'))]\n"
        "print(bad)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60, check=True
    )
    assert out.stdout.strip() == "[]"


def test_concurrent_calls_restart_a_dead_worker_once(dirs):
    with EngineClient(None, engine="fake", auto_restart=True, **dirs) as client:
        client.build("kuhn_poker")
        os.kill(client.worker_pid, signal.SIGKILL)
        client._worker.wait_exit(5.0)
        errors = []

        def call():
            try:
                client.progress()
            except WorkerDiedError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(errors) == 4 and client._generation == 1
        assert client.is_alive() and client.progress().state == "unbuilt"


def test_error_responses_carry_the_request_id(client, dirs):
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(dirs["socket_path"])
        sock.sendall(b'{"jsonrpc": "2.0", "id": 41, "method": "nope"}\n')
        with sock.makefile("rb") as stream:
            message = protocol.read_message(stream)
    assert message["id"] == 41 and message["error"]["code"] == protocol.ERROR_METHOD_NOT_FOUND


def test_lock_file_is_not_followed_through_a_symlink(dirs, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    os.symlink(str(victim), dirs["socket_path"] + ".lock")
    with pytest.raises(WorkerStartError):
        EngineClient(None, engine="fake", start_timeout=10, **dirs)
    assert victim.read_text() == "keep me"


def test_spot_build_is_validated_and_resolved(client):
    from holdem_solver.client import SpotConfig

    spot = SpotConfig("KsJsTh7d2c", "AhQh, 4c3c", "AhJd, 9d9h", pot=10, stack=10, bet_sizes="pot")
    for game in (spot, spot.to_dict()):
        built = client.build(game)
        assert built.game == "spot_river"
        assert built.spot["num_deals"] == 3 and built.spot["num_blocked_pairs"] == 1
    assert client.build("kuhn_poker").spot is None
    # A bad spot fails in the client, before anything is sent.
    with pytest.raises(ValueError, match="unknown spot fields"):
        client.build(dict(spot.to_dict(), raises=True))
    assert client.progress().state == "idle"
