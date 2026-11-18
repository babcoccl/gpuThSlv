"""Prometheus metrics for ``GET /metrics``.

Latency histograms are observed by the scheduler as jobs move through their
phases. Everything else (job counts, queue depth, GPU utilization and memory,
iterations/sec, the VRAM factor, worker restarts) is read from the job store
and scheduler state at scrape time, so it is always consistent with the API.

Each app gets its own ``CollectorRegistry``: several apps in one process
(tests) never share or double-register metrics.
"""

from __future__ import annotations

from typing import Any, Iterable

from prometheus_client import CollectorRegistry, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from . import gpus as gpu_stats

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

STATUSES = ("queued", "building", "solving", "exporting", "done", "cancelled", "failed")
ERROR_CODES = (
    "cuda_oom", "worker_died", "worker_hung", "timeout", "disk_full", "storage_error",
    "engine_error", "vram", "api_restart", "internal",
)
# Measured build times run from ~0.4 s (2k nodes) to ~100 s (9.6M nodes).
BUILD_BUCKETS = (0.25, 0.5, 1, 2, 5, 10, 20, 40, 80, 160, 320, 640)
SOLVE_BUCKETS = (0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200)


def _gpu_label(gpu: Any) -> str:
    return "cpu" if gpu is None else str(gpu)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.build_seconds = Histogram(
            "holdem_build_duration_seconds", "Worker build time (tree + solver allocation) per job.",
            ["gpu", "dtype"], buckets=BUILD_BUCKETS, registry=self.registry,
        )
        self.solve_seconds = Histogram(
            "holdem_solve_duration_seconds", "Wall time from claim to terminal state per job, by outcome.",
            ["gpu", "status"], buckets=SOLVE_BUCKETS, registry=self.registry,
        )

    def observe_build(self, gpu: Any, dtype: str | None, seconds: float | None) -> None:
        if seconds is not None:
            self.build_seconds.labels(_gpu_label(gpu), dtype or "float32").observe(float(seconds))

    def observe_solve(self, gpu: Any, status: str, seconds: float | None) -> None:
        if seconds is not None:
            self.solve_seconds.labels(_gpu_label(gpu), status).observe(float(seconds))

    def attach(self, store: Any, scheduler: Any) -> None:
        self.registry.register(_StateCollector(store, scheduler))

    def render(self) -> bytes:
        return generate_latest(self.registry)


class _StateCollector(Collector):
    def __init__(self, store: Any, scheduler: Any) -> None:
        self._store = store
        self._scheduler = scheduler

    def describe(self) -> Iterable[Any]:
        return []  # collected at scrape time; skip the describe-time collect

    def collect(self) -> Iterable[Any]:
        counts = self._store.counts()
        jobs = GaugeMetricFamily("holdem_jobs", "Jobs in the store by status.", labels=["status"])
        for status in STATUSES:
            jobs.add_metric([status], counts["status"].get(status, 0))
        yield jobs
        failed = GaugeMetricFamily("holdem_jobs_failed", "Failed jobs in the store by error_code.", labels=["error_code"])
        for code in sorted(set(ERROR_CODES) | set(counts["error_code"])):
            failed.add_metric([code], counts["error_code"].get(code, 0))
        yield failed
        yield GaugeMetricFamily("holdem_queue_depth", "Queued jobs waiting for a GPU.", value=counts["status"].get("queued", 0))

        factor = GaugeMetricFamily(
            "holdem_vram_factor", "Admission factor applied to the VRAM estimate (seed until enough samples).",
            labels=["dtype", "source"],
        )
        samples = GaugeMetricFamily("holdem_vram_factor_samples", "Qualifying (>=64 MiB) peak samples.", labels=["dtype"])
        for dtype in ("float32", "float64"):
            value, source, count = self._scheduler.vram.factor(dtype)
            factor.add_metric([dtype, source], value)
            samples.add_metric([dtype], count)
        yield factor
        yield samples

        restarts = CounterMetricFamily("holdem_worker_restarts", "Engine worker respawns since API start.", labels=["gpu"])
        busy = GaugeMetricFamily("holdem_gpu_busy", "1 while a job runs on the GPU.", labels=["gpu"])
        healthy = GaugeMetricFamily("holdem_worker_healthy", "1 if the GPU's worker is accepting jobs.", labels=["gpu"])
        ips = GaugeMetricFamily(
            "holdem_iterations_per_second", "Solver iterations/sec of the running (else the last) job.", labels=["gpu"]
        )
        util = GaugeMetricFamily("holdem_gpu_utilization_ratio", "NVML GPU utilization (0-1).", labels=["gpu"])
        mem_util = GaugeMetricFamily("holdem_gpu_memory_utilization_ratio", "NVML memory-bus utilization (0-1).", labels=["gpu"])
        used = GaugeMetricFamily("holdem_gpu_memory_used_bytes", "NVML device memory used.", labels=["gpu"])
        total = GaugeMetricFamily("holdem_gpu_memory_total_bytes", "NVML device memory total.", labels=["gpu"])
        for gpu, st in self._scheduler.gpu_metrics().items():
            label = _gpu_label(gpu)
            restarts.add_metric([label], st["restarts"])
            busy.add_metric([label], 1 if st["job_id"] else 0)
            healthy.add_metric([label], 1 if st["healthy"] else 0)
            if st["iterations_per_second"] is not None:
                ips.add_metric([label], st["iterations_per_second"])
            stats = gpu_stats.gpu_stats(gpu) if gpu is not None else None
            if stats:
                util.add_metric([label], stats["utilization_gpu_pct"] / 100.0)
                mem_util.add_metric([label], stats["utilization_memory_pct"] / 100.0)
                used.add_metric([label], stats["memory_used_bytes"])
                total.add_metric([label], stats["memory_total_bytes"])
        yield from (restarts, busy, healthy, ips, util, mem_util, used, total)
