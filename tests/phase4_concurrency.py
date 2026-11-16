"""10 mixed jobs on two GPUs with a SIGKILL chaos event; shared by the fake
and GPU tests and by scripts/phase4/concurrency_gate.py."""

import os
import signal
import threading
import time

from phase4_util import TERMINAL, poll_until, wait_for


class Sampler(threading.Thread):
    """Samples the job table every ``interval`` seconds and records the
    maximum number of building/solving jobs seen on each GPU at once."""

    def __init__(self, store, interval=0.05):
        super().__init__(daemon=True)
        self.store = store
        self.interval = interval
        self.max_running = {}
        self.samples = 0
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            counts = {}
            for job in self.store.all_jobs():
                if job["status"] in ("building", "solving"):
                    counts[job["gpu"]] = counts.get(job["gpu"], 0) + 1
            for gpu, n in counts.items():
                self.max_running[gpu] = max(self.max_running.get(gpu, 0), n)
            self.samples += 1
            time.sleep(self.interval)


def assert_no_overlap(intervals):
    by_gpu = {}
    for gpu, job_id, start, end in intervals:
        by_gpu.setdefault(gpu, []).append((start, end, job_id))
    for gpu, spans in by_gpu.items():
        spans.sort()
        for (s0, e0, j0), (s1, e1, j1) in zip(spans, spans[1:]):
            assert s1 >= e0, f"gpu {gpu} ran {j0} and {j1} at the same time"
    return {gpu: len(spans) for gpu, spans in by_gpu.items()}


def run_mixed(client, payloads, *, chaos_index, timeout=600.0, log=print):
    """Submit ``payloads`` (10 mixed jobs); SIGKILL the worker running
    ``payloads[chaos_index]`` once it is solving. Returns (jobs, report)."""
    store = client.app.state.store
    scheduler = client.app.state.scheduler
    sampler = Sampler(store)
    sampler.start()
    t0 = time.time()
    ids = []
    for payload in payloads:
        response = client.post("/v1/solves", json=payload)
        assert response.status_code == 202, response.text
        ids.append(response.json()["id"])
    victim = ids[chaos_index]
    wait_for(lambda: client.get(f"/v1/solves/{victim}").json()["status"] == "solving",
             timeout=timeout, interval=0.05, message="chaos victim solving")
    time.sleep(0.5)
    gpu = client.get(f"/v1/solves/{victim}").json()["gpu"]
    pid = scheduler.client(gpu).worker_pid
    os.kill(pid, signal.SIGKILL)
    killed_at = time.time() - t0
    log(f"chaos: SIGKILL worker pid {pid} on gpu {gpu} running job {victim} at t={killed_at:.2f}s")
    jobs = [poll_until(client, job_id, timeout=timeout) for job_id in ids]
    # A runner records its interval after the job turns terminal (GPU freed last).
    wait_for(lambda: len(scheduler.intervals) >= len(ids), timeout=30, message="all runners released")
    sampler.stop.set()
    sampler.join()
    per_gpu = assert_no_overlap(scheduler.intervals)
    report = {
        "wall_seconds": time.time() - t0,
        "jobs_per_gpu": per_gpu,
        "max_concurrent_per_gpu": sampler.max_running,
        "samples": sampler.samples,
        "chaos": {"job": victim, "gpu": gpu, "pid": pid, "t": killed_at},
    }
    assert all(n <= 1 for n in sampler.max_running.values()), sampler.max_running
    assert all(job["status"] in TERMINAL for job in jobs)
    for job in jobs:
        if job["status"] == "failed":
            assert job["error_code"] and job["error"], job  # clean, classified failure
    victim_job = jobs[chaos_index]
    assert victim_job["status"] == "failed" and victim_job["error_code"] == "worker_died", victim_job
    assert "exited with code -9" in victim_job["error"], victim_job["error"]
    return jobs, report
