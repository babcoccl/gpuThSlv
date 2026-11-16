"""Phase 2A gate: .venv-api client <-> .venv-engine worker, real GPU-CFR."""

import os
import shutil
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from holdem_solver.client import EngineClient, NotBuiltError, WorkerDiedError
from holdem_solver.client.process import default_worker_python

KUHN_VALUE_P0 = -1.0 / 18.0

pytestmark = pytest.mark.gpu


def _gpu_count():
    if shutil.which("nvidia-smi") is None or not os.path.exists(default_worker_python("real")):
        return 0
    out = subprocess.run(["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"], capture_output=True, text=True)
    return len(out.stdout.split()) if out.returncode == 0 else 0


GPU_COUNT = _gpu_count()
each_gpu = pytest.mark.parametrize(
    "gpu",
    [pytest.param(i, marks=pytest.mark.skipif(GPU_COUNT <= i, reason="GPU %d not present" % i)) for i in (0, 1)],
)


def _compute_pids(gpu):
    out = subprocess.run(
        ["nvidia-smi", "-i", str(gpu), "--query-compute-apps=pid", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {int(pid) for pid in out.stdout.split()}


def _check_kuhn(client):
    built = client.build("kuhn_poker", variant="cfr_plus", seed=42)
    assert (built.num_nodes, built.num_infosets, built.num_infoset_actions) == (55, 12, 24)
    assert built.device.startswith("cuda") and built.estimated_vram_bytes > 0
    result = client.solve(iterations=2000)
    assert result.total_iterations == 2000
    assert result.exploitability < 0.005
    assert result.profile_value_p0 == pytest.approx(KUHN_VALUE_P0, abs=0.001)
    assert result.vram["peak_reserved_bytes"] > 0
    strategy = client.get_strategy()
    assert len(strategy) == 12
    for row in strategy.values():
        assert row.shape == (2,) and np.all(row >= 0)
        assert row.sum() == pytest.approx(1.0, abs=1e-5)
    return result


@each_gpu
def test_kuhn_round_trip(gpu):
    with EngineClient(gpu) as client:
        info = client.ping()
        assert info["python"].startswith("3.9.")
        assert info["cuda_visible_devices"] == str(gpu) and info["cuda_device_count"] == 1
        _check_kuhn(client)
        assert client.worker_pid in _compute_pids(gpu)
        assert client.worker_pid not in _compute_pids(1 - gpu)
        client.reset()
        assert client.progress().iteration == 0
        assert client.solve(iterations=100, progress_every=50).total_iterations == 100
        assert client.progress().exploitability_iteration == 100


@each_gpu
def test_worker_restarts_cleanly_after_kill(gpu):
    with EngineClient(gpu) as client:
        first = _check_kuhn(client)
        old_pid = client.worker_pid
        os.kill(old_pid, signal.SIGKILL)
        with pytest.raises(WorkerDiedError):
            client.solve(iterations=10)
        info = client.restart()
        assert info["pid"] != old_pid and info["state"] == "unbuilt"
        with pytest.raises(NotBuiltError):
            client.get_strategy()
        second = _check_kuhn(client)
        assert second.exploitability == pytest.approx(first.exploitability, rel=0.05)
        # The killed worker's CUDA context is gone; only the new one holds the GPU.
        deadline = time.monotonic() + 10
        while old_pid in _compute_pids(gpu) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert old_pid not in _compute_pids(gpu)
        assert client.worker_pid in _compute_pids(gpu)


@each_gpu
def test_cancel_real_solve(gpu):
    with EngineClient(gpu) as client:
        client.build("kuhn_poker")
        client.solve(target_exploitability=1e-12, chunk_iterations=50, wait=False)
        time.sleep(0.5)
        running = client.progress()
        assert running.state == "solving" and running.iteration > 0
        assert client.cancel() is True
        result = client.wait(timeout=60)
        assert result.cancelled is True and result.target_reached is False
        assert client.solve(iterations=50).iterations_run == 50


@pytest.mark.skipif(GPU_COUNT < 2, reason="needs two GPUs")
def test_two_workers_solve_concurrently():
    with EngineClient(0) as first, EngineClient(1) as second:
        assert first.ping()["cuda_visible_devices"] == "0"
        assert second.ping()["cuda_visible_devices"] == "1"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_check_kuhn, (first, second)))
        assert results[0].exploitability == pytest.approx(results[1].exploitability, rel=0.05)
