"""Phase 4 concurrency + chaos test on the real engine; needs both GPUs."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402
from holdem_solver.api.scheduler import SchedulerConfig  # noqa: E402

from phase4_concurrency import run_mixed  # noqa: E402

BOARD = "KsJsTh7d2c"
SIZES = [  # ~2k, ~18k, ~153k nodes
    ("QQ+, AKs", "pot", 300),
    ("TT+, AQs+, AKo, KQs", "standard", 400),
    ("77+, A9s+, KTs+, QJs, ATo+, KQo", "standard", 2000),
]


@pytest.mark.gpu
def test_ten_mixed_real_jobs_two_gpus_with_sigkill(tmp_path):
    order = [2, 0, 1, 0, 2, 1, 0, 1, 2, 0]
    payloads = [
        {"board": BOARD, "range0": SIZES[s][0], "range1": SIZES[s][0], "pot": 20.0, "stack": 100.0,
         "bet_sizes": SIZES[s][1], "iterations": SIZES[s][2], "progress_every": 100, "priority": i % 3}
        for i, s in enumerate(order)
    ]
    app = create_app(gpus=[0, 1], engine="real", data_dir=str(tmp_path / "api"),
                     scheduler_config=SchedulerConfig(heartbeat_seconds=2.0))
    with TestClient(app) as client:
        jobs, report = run_mixed(client, payloads, chaos_index=4, timeout=900)
    assert sum(report["jobs_per_gpu"].values()) == 10
    assert [j["status"] for i, j in enumerate(jobs) if i != 4] == ["done"] * 9
