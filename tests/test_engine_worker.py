"""EngineService with the FakeEngine, in-process (no socket, no GPU)."""

import os
import shutil
import tempfile
import time

import numpy as np
import pytest

from holdem_solver import protocol
from holdem_solver.engine_worker import EngineService, FakeEngine, RpcError


@pytest.fixture
def service():
    shm_dir = tempfile.mkdtemp(prefix="holdem_solver_test_", dir=protocol.SHM_ROOT)
    svc = EngineService(FakeEngine(), shm_dir, {"engine": "fake"})
    yield svc
    svc.stop()
    shutil.rmtree(shm_dir, ignore_errors=True)


def _wait(service, timeout=10.0):
    answer = service.dispatch("solve_wait", {"timeout": timeout})
    assert answer["done"]
    return answer


def test_unbuilt_engine_refuses_work(service):
    assert service.dispatch("ping", {})["state"] == "unbuilt"
    for method in ("solve", "get_strategy", "reset"):
        with pytest.raises(RpcError) as excinfo:
            service.dispatch(method, {})
        assert excinfo.value.code == protocol.ERROR_NOT_BUILT
    with pytest.raises(RpcError) as excinfo:
        service.dispatch("solve_wait", {})
    assert excinfo.value.code == protocol.ERROR_NO_JOB


def test_build_solve_strategy_reset(service):
    info = service.dispatch("build", {"game": "kuhn_poker"})
    assert (info["num_nodes"], info["num_infosets"], info["num_infoset_actions"]) == (55, 12, 24)
    assert service.dispatch("solve", {"iterations": 200})["state"] == "solving"
    answer = _wait(service)
    assert answer["state"] == "done"
    assert answer["result"]["total_iterations"] == 200
    assert service.dispatch("progress", {})["iteration"] == 200

    refs = service.dispatch("get_strategy", {})
    offsets = np.load(refs["offsets"][protocol.ARRAY_REF_KEY])
    values = np.load(refs["values"][protocol.ARRAY_REF_KEY])
    keys = np.load(refs["keys"][protocol.ARRAY_REF_KEY])
    assert refs["num_infosets"] == len(keys) == 12
    assert offsets.tolist() == list(range(0, 25, 2))
    assert values.shape == (24,) and refs["values"]["shape"] == [24]
    np.testing.assert_allclose(values.reshape(12, 2).sum(axis=1), 1.0, rtol=1e-6)

    # An uncollected strategy is replaced rather than piling up in /dev/shm.
    first_files = sorted(os.listdir(os.path.dirname(refs["values"][protocol.ARRAY_REF_KEY])))
    service.dispatch("get_strategy", {})
    second_files = sorted(os.listdir(os.path.dirname(refs["values"][protocol.ARRAY_REF_KEY])))
    assert len(first_files) == len(second_files) == 3 and first_files != second_files

    assert service.dispatch("reset", {}) == {"iteration": 0}
    assert service.dispatch("progress", {})["state"] == "idle"


def test_busy_while_solving_then_cancel(service):
    service.dispatch("build", {"game": "kuhn_poker", "options": {"fake_iter_seconds": 0.002}})
    service.dispatch("solve", {"iterations": 100000, "chunk_iterations": 5})
    for method, params in (("build", {"game": "kuhn_poker"}), ("solve", {}), ("get_strategy", {}), ("reset", {})):
        with pytest.raises(RpcError) as excinfo:
            service.dispatch(method, params)
        assert excinfo.value.code == protocol.ERROR_BUSY
    assert service.dispatch("solve_wait", {"timeout": 0.05})["done"] is False
    time.sleep(0.05)
    progress = service.dispatch("progress", {})
    assert progress["state"] == "solving" and 0 < progress["iteration"] < 100000
    assert service.dispatch("cancel", {})["cancel_requested"] is True
    answer = _wait(service)
    assert answer["state"] == "cancelled" and answer["result"]["cancelled"] is True
    assert 0 < answer["result"]["iterations_run"] < 100000
    assert service.dispatch("cancel", {})["cancel_requested"] is False
    # The engine is free again and the solve can be resumed.
    service.dispatch("solve", {"iterations": 10})
    assert _wait(service)["result"]["iterations_run"] == 10


def test_progress_reports_exploitability_when_requested(service):
    service.dispatch("build", {"game": "kuhn_poker"})
    service.dispatch("solve", {"iterations": 300, "progress_every": 100})
    _wait(service)
    progress = service.dispatch("progress", {})
    assert progress["exploitability_iteration"] == 300
    assert progress["exploitability"] == pytest.approx(0.5 / (301 ** 0.5))


def test_engine_errors_keep_worker_usable(service):
    service.dispatch("build", {"game": "kuhn_poker", "options": {"fake_fail_solve": True}})
    service.dispatch("solve", {"iterations": 10})
    with pytest.raises(RpcError) as excinfo:
        service.dispatch("solve_wait", {"timeout": 10})
    assert excinfo.value.code == protocol.ERROR_ENGINE
    assert excinfo.value.data["type"] == "RuntimeError"
    assert "fake solve failure" in excinfo.value.data["traceback"]
    assert service.dispatch("progress", {})["state"] == "failed"

    with pytest.raises(RpcError) as excinfo:
        service.dispatch("build", {"game": "no_such_game"})
    assert excinfo.value.code == protocol.ERROR_INVALID_PARAMS
    for method, params in (("solve", {"bogus": 1}), ("build", {}), ("progress", {"x": 1})):
        with pytest.raises(RpcError) as excinfo:
            service.dispatch(method, params)
        assert excinfo.value.code == protocol.ERROR_INVALID_PARAMS

    service.dispatch("build", {"game": "kuhn_poker"})
    service.dispatch("solve", {"iterations": 10})
    assert _wait(service)["state"] == "done"


def test_close_cancels_and_releases(service):
    service.dispatch("build", {"game": "kuhn_poker", "options": {"fake_iter_seconds": 0.002}})
    service.dispatch("solve", {"target_exploitability": 1e-9, "chunk_iterations": 5})
    assert service.dispatch("close", {})["state"] == "unbuilt"
    assert service.dispatch("ping", {})["built"] is False
