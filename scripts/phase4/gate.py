"""Phase 4 GPU gate on the real engine (run with .venv-api).

  --mode concurrency  10 mixed-size jobs on GPUs 0+1, SIGKILL one worker mid-solve
  --mode oom          hog VRAM on one GPU from another process, then solve -> cuda_oom + respawn
  --mode hung         SIGSTOP a real worker mid-solve -> watchdog kill + respawn

Writes one JSON line per job / event to stdout (redirect to scripts/phase4/logs/).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from holdem_solver.api import create_app  # noqa: E402
from holdem_solver.api.scheduler import SchedulerConfig  # noqa: E402
from phase4_concurrency import run_mixed  # noqa: E402
from phase4_util import poll_until, wait_for  # noqa: E402

BOARD = "KsJsTh7d2c"
NARROW = "QQ+, AKs"
MID = "TT+, AQs+, AKo, KQs"
WIDE = "77+, A9s+, KTs+, QJs, ATo+, KQo"
BROAD = "22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo"

# (range0, range1, bet_sizes, iterations): 4 sizes from ~1e3 to ~6e5 nodes.
SIZES = [
    (NARROW, NARROW, "pot", 300),
    (MID, MID, "standard", 400),
    (WIDE, WIDE, "standard", 500),
    (BROAD, BROAD, "standard", 600),
]


def emit(**record):
    print(json.dumps(record), flush=True)


def spot(r0, r1, bets, iterations, **extra):
    return {"board": BOARD, "range0": r0, "range1": r1, "pot": 20.0, "stack": 100.0,
            "bet_sizes": bets, "iterations": iterations, "progress_every": 100, **extra}


def summarize(job):
    keys = ("id", "gpu", "status", "error_code", "error", "priority", "num_nodes", "build_seconds",
            "elapsed_seconds", "iterations_run", "exploitability", "estimated_vram_bytes",
            "predicted_vram_bytes", "peak_vram_bytes")
    return {k: job.get(k) for k in keys}


def mode_concurrency(args):
    order = [3, 0, 1, 2, 0, 3, 1, 2, 0, 1]  # mixed sizes, BROAD first so it overlaps everything
    payloads = [spot(*SIZES[s], priority=i % 3) for i, s in enumerate(order)]
    app = create_app(gpus=[0, 1], engine="real", data_dir=tempfile.mkdtemp(prefix="p4gate-"),
                     scheduler_config=SchedulerConfig(heartbeat_seconds=2.0, hung_worker_seconds=60.0))
    with TestClient(app) as client:
        jobs, report = run_mixed(client, payloads, chaos_index=args.chaos_index, timeout=1800,
                                 log=lambda m: emit(event="chaos", msg=m))
        for i, job in enumerate(jobs):
            emit(event="job", index=i, size=order[i], **summarize(job))
        emit(event="intervals", intervals=[
            {"gpu": g, "job": j, "start": round(s, 3), "end": round(e, 3)} for g, j, s, e in client.app.state.scheduler.intervals
        ])
        gpus = client.get("/v1/gpus").json()
        emit(event="gpus", gpus=gpus)
        emit(event="report", **report)


HOG = """
import sys, time, torch
gib = float(sys.argv[1])
x = torch.empty(int(gib * 1024**3), dtype=torch.uint8, device="cuda")
print("ready", torch.cuda.mem_get_info(), flush=True)
time.sleep(3600)
"""


def mode_oom(args):
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(args.gpu)}
    hog = subprocess.Popen([os.path.join(ROOT, ".venv-engine/bin/python"), "-c", HOG, str(args.hog_gib)],
                           env=env, stdout=subprocess.PIPE, text=True)
    try:
        emit(event="hog", line=hog.stdout.readline().strip(), gib=args.hog_gib)
        app = create_app(gpus=[args.gpu], engine="real", data_dir=tempfile.mkdtemp(prefix="p4oom-"),
                         scheduler_config=SchedulerConfig(heartbeat_seconds=2.0))
        with TestClient(app) as client:
            pid0 = client.app.state.scheduler.client(args.gpu).worker_pid
            t0 = time.time()
            job = client.post("/v1/solves", json=spot(BROAD, BROAD, "standard", 200)).json()
            job = poll_until(client, job["id"], timeout=600)
            emit(event="oom_job", seconds=time.time() - t0, **summarize(job))
            wait_for(lambda: client.get("/v1/gpus").json()[0]["healthy"], timeout=120, message="respawn")
            pid1 = client.app.state.scheduler.client(args.gpu).worker_pid
            emit(event="respawn", old_pid=pid0, new_pid=pid1)
            hog.kill()
            hog.wait()
            job = client.post("/v1/solves", json=spot(BROAD, BROAD, "standard", 200)).json()
            job = poll_until(client, job["id"], timeout=600)
            emit(event="after_hog", **summarize(job))
    finally:
        if hog.poll() is None:
            hog.kill()


def mode_hung(args):
    config = SchedulerConfig(heartbeat_seconds=1.0, hung_worker_seconds=args.hung_seconds)
    app = create_app(gpus=[args.gpu], engine="real", data_dir=tempfile.mkdtemp(prefix="p4hung-"),
                     scheduler_config=config)
    with TestClient(app) as client:
        scheduler = client.app.state.scheduler
        job = client.post("/v1/solves", json=spot(BROAD, BROAD, "standard", 100000)).json()
        poll_until(client, job["id"], statuses=("solving",), timeout=600)
        time.sleep(3)
        pid = scheduler.client(args.gpu).worker_pid
        os.kill(pid, signal.SIGSTOP)
        t0 = time.time()
        done = poll_until(client, job["id"], timeout=600)
        failed_after = time.time() - t0
        wait_for(lambda: scheduler.client(args.gpu).worker_pid not in (None, pid), timeout=120, message="respawn")
        emit(event="hung_job", stopped_pid=pid, failed_after_seconds=failed_after,
             new_pid=scheduler.client(args.gpu).worker_pid, configured=args.hung_seconds, **summarize(done))
        job = client.post("/v1/solves", json=spot(NARROW, NARROW, "pot", 200)).json()
        emit(event="after_respawn", **summarize(poll_until(client, job["id"], timeout=600)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["concurrency", "oom", "hung"], required=True)
    parser.add_argument("--gpu", type=int, default=1)
    parser.add_argument("--chaos-index", type=int, default=5)
    parser.add_argument("--hog-gib", type=float, default=15.2)
    parser.add_argument("--hung-seconds", type=float, default=10.0)
    args = parser.parse_args()
    {"concurrency": mode_concurrency, "oom": mode_oom, "hung": mode_hung}[args.mode](args)


if __name__ == "__main__":
    main()
