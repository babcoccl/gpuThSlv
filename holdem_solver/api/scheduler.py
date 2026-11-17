"""Job scheduler: one job at a time per GPU, priority queue, watchdog.

The queue lives in SQLite (``JobStore``), so queued jobs survive an API
restart. A dispatcher thread walks queued jobs in priority order (highest
first, FIFO within a priority) and gives each to the least-loaded idle,
healthy GPU whose VRAM capacity fits the job's *predicted* peak
(``VramModel``: static estimate x a factor learned from measured peaks). A job
that fits no idle GPU reserves the GPUs it could use, so lower-priority jobs
cannot starve it; they may still backfill GPUs it cannot use.

A runner thread per job builds, solves, polls ``progress()``, exports the
strategy atomically to ``<data_dir>/strategy-<id>.npz`` and releases the
solver. Failures are classified into an ``error_code`` with a clear message:

* ``cuda_oom``: CUDA ran out of memory; the worker is respawned.
* ``worker_died``: the worker process exited (crash, SIGKILL); respawned.
* ``worker_hung``: no ``ping`` answer for ``hung_worker_seconds``; the
  heartbeat watchdog SIGKILLs and respawns it.
* ``timeout``: the job ran past ``timeout_seconds``; it is cancelled, and the
  worker is killed if it does not stop within ``cancel_grace_seconds`` (or if
  it is still building).
* ``disk_full`` / ``storage_error``: the strategy could not be written.
* ``engine_error``: the engine rejected the job.
* ``internal``: a scheduler bug; see the API log.

The dispatcher tick also enforces job deadlines and deletes strategy files
older than ``retention_seconds``.

Worker modes (``SchedulerConfig.worker_mode``):

* ``spawn`` (default): the API spawns one worker per GPU with
  ``--parent-pid``, so workers exit when the API dies.
* ``external``: workers are started elsewhere (one systemd unit per GPU,
  ``Restart=always``) and the API attaches to their sockets. Recovery kills
  the worker's pid and waits for its supervisor to start a new one; restarting
  the API leaves the workers (and their CUDA contexts) up.
"""

from __future__ import annotations

import errno
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from holdem_solver import protocol
from holdem_solver.client import (
    EngineClient,
    EngineOOMError,
    EngineTimeoutError,
    RemoteEngineError,
    WorkerDiedError,
    WorkerStartError,
)

from . import gpus as gpu_stats
from .store import JobStore
from .vram import VramModel

LOG = logging.getLogger("holdem_solver.api")

_POLL_SECONDS = 1.0
_GIB = 1024**3


@dataclass
class SchedulerConfig:
    heartbeat_seconds: float = 5.0
    # Pings take ~11 s while the worker builds a 9.6M-node tree (Phase 4
    # measurement), so the default leaves a wide margin.
    hung_worker_seconds: float = 120.0
    default_timeout_seconds: float | None = None
    cancel_grace_seconds: float = 30.0
    retention_seconds: float | None = 7 * 24 * 3600.0
    cleanup_interval_seconds: float = 300.0
    vram_reserve_bytes: int = 512 * 1024**2
    # Per-GPU VRAM capacity override; otherwise NVML total memory.
    gpu_vram_bytes: dict[int | None, int] | None = None
    worker_mode: str = "spawn"


class StrategySaveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _JobFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _Gpu:
    gpu: int | None
    client: EngineClient | None = None
    job_id: str | None = None
    phase: str = "idle"
    deadline: float | None = None
    timeout_seconds: float | None = None
    timeout_fired_at: float | None = None
    kill_reason: str | None = None
    killing: bool = False
    healthy: bool = True
    last_heartbeat: float | None = None
    last_heartbeat_seconds: float | None = None
    completed: int = 0
    capacity: int | None = None
    iterations_per_second: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Scheduler:
    def __init__(
        self,
        gpus: list[int | None],
        store: JobStore,
        data_dir: str,
        *,
        engine: str = "real",
        client_kwargs: dict[int | None, dict[str, Any]] | None = None,
        client_factory: Callable[..., EngineClient] = EngineClient,
        config: SchedulerConfig | None = None,
        metrics: Any = None,
    ) -> None:
        self._store = store
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._engine = engine
        self._client_kwargs = client_kwargs or {}
        self._client_factory = client_factory
        self.config = config or SchedulerConfig()
        if self.config.worker_mode not in ("spawn", "external"):
            raise ValueError("worker_mode must be 'spawn' or 'external'")
        self.metrics = metrics
        self.vram = VramModel(store)
        self._gpus = list(gpus)
        self._state: dict[int | None, _Gpu] = {gpu: _Gpu(gpu) for gpu in self._gpus}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="scheduler", daemon=True)
        self._watchdogs: list[threading.Thread] = []
        self._runners: list[threading.Thread] = []
        self._last_cleanup = 0.0
        # Injection point for disk-full tests (e.g. open /dev/full).
        self._open_for_write: Callable[[str], Any] = lambda path: open(path, "wb")
        # Every (gpu, job_id, start, end) runner interval, for tests and gates.
        self.intervals: list[tuple[int | None, str, float, float]] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        external = self.config.worker_mode == "external"
        for gpu in self._gpus:
            st = self._state[gpu]
            st.client = self._client_factory(
                # auto_restart=True: a fresh unbuilt worker is spawned (or, for
                # an external worker, awaited) before WorkerDiedError
                # propagates, so a crash does not permanently disable the GPU.
                gpu, engine=self._engine, auto_restart=True, spawn=not external,
                **self._client_kwargs.get(gpu, {})
            )
            if external:
                # Attaching can wait up to start_timeout for the worker; do it on
                # the watchdog thread so startup (and /healthz) is not blocked.
                st.healthy = False
            st.last_heartbeat = time.monotonic()
            st.capacity = self._capacity(gpu)
            target = self._attach_then_watch if external else self._watchdog_loop
            thread = threading.Thread(target=target, args=(st,), name=f"watchdog-{gpu}", daemon=True)
            self._watchdogs.append(thread)
        self._dispatcher.start()
        for thread in self._watchdogs:
            thread.start()

    def _attach_then_watch(self, st: _Gpu) -> None:
        self._attach(st)
        self._watchdog_loop(st)

    def _attach(self, st: _Gpu) -> None:
        try:
            info = st.client.start()
            LOG.info("gpu %s: attached to external worker pid %s", st.gpu, info.get("pid"))
            # A previous API process may have left a built solver behind.
            st.client.close()
            st.healthy = True
        except Exception as exc:
            LOG.warning("gpu %s: external worker not available (%s); marked unhealthy", st.gpu, exc)
            st.healthy = False
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._dispatcher.join(timeout=5)
        for thread in self._runners + self._watchdogs:
            thread.join(timeout=5)
        for st in self._state.values():
            if st.client is None:
                continue
            try:
                st.client.shutdown()
            except Exception:
                LOG.warning("worker shutdown failed", exc_info=True)

    def _capacity(self, gpu: int | None) -> int | None:
        if self.config.gpu_vram_bytes is not None and gpu in self.config.gpu_vram_bytes:
            return int(self.config.gpu_vram_bytes[gpu])
        if gpu is None or self._engine != "real":
            return None
        stats = gpu_stats.gpu_stats(gpu)
        return None if not stats else int(stats["memory_total_bytes"])

    # -- public surface used by the app -------------------------------------

    def wake(self) -> None:
        self._wake.set()

    def client(self, gpu: int | None) -> EngineClient | None:
        return self._state[gpu].client if gpu in self._state else None

    @property
    def gpus(self) -> list[int | None]:
        return list(self._gpus)

    def usable_vram(self, gpu: int | None) -> int | None:
        cap = self._state[gpu].capacity
        return None if cap is None else cap - self.config.vram_reserve_bytes

    def max_usable_vram(self) -> int | None:
        caps = [self.usable_vram(gpu) for gpu in self._gpus]
        if not caps or any(cap is None for cap in caps):
            return None
        return max(caps)

    def gpu_status(self, gpu: int | None) -> dict[str, Any]:
        """Worker ping + running job + watchdog state for one GPU."""
        st = self._state[gpu]
        info: dict[str, Any] = {"gpu": gpu, "state": "unavailable"}
        if st.client is not None:
            try:
                ping = st.client.ping(timeout=5.0)
                info.update(
                    {
                        "state": ping.get("state"),
                        "device": ping.get("cuda_device_name") or ping.get("device"),
                        "worker_pid": ping.get("pid"),
                        "worker_vram": ping.get("vram") or {},
                    }
                )
            except Exception:
                info["state"] = "unavailable"
        info["job_id"] = st.job_id
        info["healthy"] = st.healthy
        info["restarts"] = self._restarts(st)
        info["vram_capacity_bytes"] = st.capacity
        info["heartbeat_age_seconds"] = (
            None if st.last_heartbeat is None else round(time.monotonic() - st.last_heartbeat, 3)
        )
        info["heartbeat_latency_seconds"] = st.last_heartbeat_seconds
        return info

    @staticmethod
    def _restarts(st: _Gpu) -> int:
        # The client bumps its generation on every worker restart, whichever
        # path (watchdog, runner, OOM respawn, external supervisor) caused it.
        return 0 if st.client is None else st.client.generation

    def gpu_metrics(self) -> dict[int | None, dict[str, Any]]:
        """Scheduler-side per-GPU state for /metrics (no worker RPCs)."""
        return {
            gpu: {
                "restarts": self._restarts(st),
                "job_id": st.job_id,
                "healthy": st.healthy and not st.killing,
                "iterations_per_second": st.iterations_per_second,
            }
            for gpu, st in self._state.items()
        }

    def estimate(self, spot: dict[str, Any], dtype: str = "float32") -> dict[str, Any]:
        """Node count + VRAM estimate for a spot at ``dtype``, from any live worker."""
        last_error: Exception | None = None
        for gpu in self._gpus:
            client = self._state[gpu].client
            if client is None:
                continue
            try:
                return client.estimate_spot(spot, dtype=dtype)
            except RemoteEngineError:
                raise
            except Exception as exc:  # worker down or busy building; try the next GPU
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("no engine workers are running")

    # -- dispatch ------------------------------------------------------------

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._enforce_deadlines()
                self._maybe_cleanup()
                if self._dispatch_once():
                    continue
            except Exception:
                LOG.exception("dispatcher tick failed")
            self._wake.wait(timeout=_POLL_SECONDS)
            self._wake.clear()

    def _fits(self, job: dict[str, Any], gpu: int | None) -> bool:
        usable = self.usable_vram(gpu)
        need = job.get("predicted_vram_bytes")
        return usable is None or need is None or need <= usable

    def _dispatch_once(self) -> bool:
        """Start at most one job; true if one was started."""
        with self._lock:
            idle = [g for g in self._gpus if self._state[g].job_id is None and self._state[g].healthy
                    and not self._state[g].killing]
        if not idle:
            return False
        for job in self._store.queued():
            candidates = [g for g in idle if self._fits(job, g)]
            if candidates:
                gpu = min(candidates, key=lambda g: (self._state[g].completed, self._gpus.index(g)))
                if self._store.claim(job["id"], gpu):
                    self._start_runner(job, gpu)
                    return True
                continue  # cancelled meanwhile
            # No idle GPU fits: the job waits for a busy one. Lower-priority
            # jobs may backfill idle GPUs it cannot use; a GPU it can use is
            # offered to it first when it frees up (strict priority order).
            if all(not self._fits(job, g) for g in self._gpus):
                # Capacity shrank (e.g. a smaller GPU set) after admission.
                self._store.finish(
                    job["id"], "failed", error_code="vram",
                    error=f"predicted peak VRAM {job['predicted_vram_bytes'] / _GIB:.2f} GiB "
                    "does not fit any configured GPU",
                )
                continue
        return False

    def _start_runner(self, job: dict[str, Any], gpu: int | None) -> None:
        st = self._state[gpu]
        timeout = job.get("timeout_seconds") or self.config.default_timeout_seconds
        with self._lock:
            st.job_id = job["id"]
            st.phase = "building"
            st.timeout_seconds = timeout
            st.deadline = None if not timeout else time.monotonic() + float(timeout)
            st.timeout_fired_at = None
            st.kill_reason = None
        thread = threading.Thread(target=self._run, args=(job["id"], gpu), name="job-%s" % job["id"], daemon=True)
        self._runners = [t for t in self._runners if t.is_alive()]
        self._runners.append(thread)
        thread.start()

    # -- deadlines, watchdog, kill -----------------------------------------

    def _enforce_deadlines(self) -> None:
        now = time.monotonic()
        for st in self._state.values():
            if st.job_id is None or st.deadline is None or now < st.deadline or st.killing:
                continue
            if st.timeout_fired_at is None:
                st.timeout_fired_at = now
                LOG.warning("job %s on gpu %s exceeded timeout_seconds=%s while %s",
                            st.job_id, st.gpu, st.timeout_seconds, st.phase)
                if st.phase == "solving":
                    self._background(self._cancel_quietly, st)
                else:
                    # A build cannot be interrupted; only a kill frees the GPU.
                    self._kill(st, "timeout")
            elif now - st.timeout_fired_at > self.config.cancel_grace_seconds:
                self._kill(st, "timeout")

    def _cancel_quietly(self, st: _Gpu) -> None:
        try:
            st.client.cancel(timeout=10.0)
        except Exception:
            LOG.warning("cancel after timeout failed on gpu %s", st.gpu, exc_info=True)

    def _background(self, fn: Callable[..., Any], *args: Any) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _kill(self, st: _Gpu, reason: str) -> None:
        """SIGKILL + respawn the worker; its runner then fails the job with ``reason``."""
        with self._lock:
            if st.killing:
                return
            st.killing = True
            st.kill_reason = reason
        LOG.warning("killing worker on gpu %s (%s, job %s)", st.gpu, reason, st.job_id)
        self._background(self._kill_and_restart, st)

    def _kill_and_restart(self, st: _Gpu) -> None:
        try:
            st.client.kill_and_restart()
            st.healthy = True
            st.last_heartbeat = time.monotonic()
        except WorkerStartError:
            LOG.exception("worker respawn failed on gpu %s; marking it unhealthy", st.gpu)
            st.healthy = False
        finally:
            with self._lock:
                st.killing = False
            self._wake.set()

    def _watchdog_loop(self, st: _Gpu) -> None:
        cfg = self.config
        while not self._stop.wait(cfg.heartbeat_seconds):
            if st.killing:
                continue
            t0 = time.monotonic()
            try:
                st.client.ping(timeout=cfg.hung_worker_seconds)
            except EngineTimeoutError:
                LOG.warning("gpu %s worker missed its heartbeat for %.1fs", st.gpu, time.monotonic() - t0)
                self._kill(st, "hung")
                continue
            except WorkerDiedError:
                # auto_restart already spawned a fresh worker (or failed to).
                st.healthy = st.client.is_alive()
                continue
            except Exception:
                LOG.warning("heartbeat failed on gpu %s", st.gpu, exc_info=True)
                continue
            st.last_heartbeat = time.monotonic()
            st.last_heartbeat_seconds = round(st.last_heartbeat - t0, 4)
            if not st.healthy:
                st.healthy = True
                self._wake.set()

    # -- retention -----------------------------------------------------------

    def _maybe_cleanup(self) -> None:
        if self.config.retention_seconds is None:
            return
        now = time.time()
        if now - self._last_cleanup < self.config.cleanup_interval_seconds:
            return
        self._last_cleanup = now
        self.cleanup_strategies(now)

    def cleanup_strategies(self, now: float | None = None) -> dict[str, int]:
        """Delete strategy files older than ``retention_seconds`` and orphans."""
        now = time.time() if now is None else now
        cutoff = now - (self.config.retention_seconds or 0.0)
        removed = expired = 0
        for job in self._store.expired_strategies(cutoff):
            current = self._store.get(job["id"])
            if current is None or current.get("pinned"):
                continue  # pinned between selection and deletion
            try:
                os.unlink(job["strategy_path"])
                removed += 1
            except FileNotFoundError:
                pass
            self._store.set_fields(job["id"], strategy_path=None, strategy_expired_at=now)
            expired += 1
        known = self._store.strategy_paths()
        orphans = 0
        for path in self._data_dir.glob("strategy-*.npz*"):
            if str(path) in known:
                continue
            try:
                if path.stat().st_mtime < cutoff or path.name.endswith(".tmp") and path.stat().st_mtime < now - 3600:
                    path.unlink()
                    orphans += 1
            except FileNotFoundError:
                pass
        if expired or orphans:
            LOG.info("retention: expired %d strategies (%d files), removed %d orphans", expired, removed, orphans)
        return {"expired": expired, "removed": removed, "orphans": orphans}

    # -- runner ---------------------------------------------------------------

    def _run(self, job_id: str, gpu: int | None) -> None:
        st = self._state[gpu]
        start = time.time()
        try:
            self._run_job(job_id, st)
        except Exception as exc:
            code, message = self._classify(exc, st)
            if code == "internal":
                LOG.exception("job %s failed", job_id)
            else:
                LOG.warning("job %s failed [%s]: %s", job_id, code, message)
            self._finish_durably(job_id, code, message, time.time() - start)
            if not st.killing:
                # The job may have died with its solve still running on the worker.
                self._release(st.client)
        finally:
            end = time.time()
            if self.metrics is not None:
                final = self._store.get(job_id) or {}
                self.metrics.observe_solve(gpu, final.get("status") or "unknown", end - start)
            # Wait for an in-progress kill so the next job gets the new worker.
            while st.killing and not self._stop.is_set():
                time.sleep(0.05)
            with self._lock:
                self.intervals.append((gpu, job_id, start, end))
                st.job_id = None
                st.phase = "idle"
                st.deadline = None
                st.timeout_fired_at = None
                st.kill_reason = None
                st.completed += 1
            self._wake.set()

    def _finish_durably(self, job_id: str, code: str, message: str, elapsed: float) -> None:
        """Record the failure, retrying while SQLite cannot write (disk full):
        giving up would leave the job 'solving' forever."""
        warned = False
        while True:
            try:
                self._store.finish(job_id, "failed", error_code=code, error=message, elapsed_seconds=elapsed)
                return
            except sqlite3.Error as exc:
                if not warned:
                    LOG.error("job %s: cannot record failure (%s); retrying until the store is writable", job_id, exc)
                    warned = True
            if self._stop.wait(1.0):
                return  # on restart the job is failed as api_restart

    def _classify(self, exc: BaseException, st: _Gpu) -> tuple[str, str]:
        phase = st.phase
        where = f"gpu {st.gpu}"
        if st.kill_reason == "hung":
            return "worker_hung", (
                f"engine worker on {where} stopped answering ping for over "
                f"{self.config.hung_worker_seconds:g}s while {phase}; it was killed and respawned"
            )
        if st.kill_reason == "timeout":
            return "timeout", (
                f"job exceeded timeout_seconds={st.timeout_seconds:g} while {phase}; "
                f"the worker on {where} was killed and respawned"
            )
        if isinstance(exc, _JobFailed):
            return exc.code, str(exc)
        if isinstance(exc, EngineOOMError):
            self._respawn(st)
            job = self._store.get(st.job_id) or {}
            predicted = job.get("predicted_vram_bytes")
            sizing = ""
            if predicted:
                cap = st.capacity
                sizing = f" (predicted peak {predicted / _GIB:.2f} GiB" + (
                    f" of {cap / _GIB:.2f} GiB)" if cap else ")"
                )
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            return "cuda_oom", (
                f"CUDA out of memory while {phase} on {where}{sizing}: {first}. "
                "The worker was respawned; narrow the spot or use float32"
            )
        if isinstance(exc, WorkerDiedError):
            return "worker_died", f"engine worker on {where} died while {phase}: {exc}"
        if isinstance(exc, EngineTimeoutError):
            # The RPC itself gave up: treat like a missed heartbeat.
            self._kill(st, "hung")
            return "worker_hung", f"engine worker on {where} stopped answering while {phase} ({exc}); it was killed and respawned"
        if isinstance(exc, sqlite3.OperationalError) and "full" in str(exc):
            return "disk_full", f"job store write failed while {phase} on {where} ({exc}); free disk space and resubmit"
        if isinstance(exc, StrategySaveError):
            return exc.code, str(exc)
        if isinstance(exc, RemoteEngineError):
            return "engine_error", str(exc)
        return "internal", "internal error running the job; see api logs"

    def _respawn(self, st: _Gpu) -> None:
        """Fresh worker after CUDA OOM: the CUDA context may be unusable."""
        try:
            with self._lock:
                st.killing = True
            st.client.restart()
        except Exception:
            LOG.exception("worker respawn after OOM failed on gpu %s", st.gpu)
            st.healthy = False
        finally:
            with self._lock:
                st.killing = False

    def _run_job(self, job_id: str, st: _Gpu) -> None:
        store = self._store
        client = st.client
        job = store.get(job_id)
        if job is None:
            return
        started = time.time()
        params = dict(job["params"])
        spot = job["spot"]

        def cancelled() -> bool:
            row = store.get(job_id)
            return bool(row and row["cancel_requested"])

        # -- build ----------------------------------------------------------
        if cancelled():
            store.finish(job_id, "cancelled")
            return
        solver_options = dict(params.pop("solver_options", {}) or {})
        solver_options["variant"] = params.pop("variant", "cfr_plus")
        st.phase = "building"
        generation = client.generation
        built = client.build(spot, **solver_options)
        st.phase = "solving"
        store.transition(
            job_id,
            "solving",
            ("building",),
            num_nodes=built.num_nodes,
            num_infosets=built.num_infosets,
            # Keep the admission-time estimate: VramModel calibrates peak/estimate
            # against it, so it must be the same number admission multiplies.
            estimated_vram_bytes=job.get("estimated_vram_bytes") or built.estimated_vram_bytes,
            measured_vram=built.vram,
            build_seconds=built.build_seconds,
        )
        if self.metrics is not None:
            self.metrics.observe_build(st.gpu, job.get("dtype"), built.build_seconds)

        # -- solve ----------------------------------------------------------
        target = params.get("target_exploitability")
        time_cap = params.pop("max_iterations", None), params.get("time_budget")
        solve_kwargs = dict(params)
        if target is not None:
            # The engine's target mode has no caps; the API's are enforced here.
            solve_kwargs.pop("time_budget", None)
        client.solve(wait=False, **solve_kwargs)
        solve_started = time.monotonic()
        st.iterations_per_second = None

        cancel_sent = False
        while True:
            time.sleep(_POLL_SECONDS)
            progress = client.progress()
            if client.generation != generation:
                # Another caller (the watchdog's ping) saw the death first
                # and restarted the worker: this poll reached the new one.
                raise WorkerDiedError(
                    f"progress: engine worker for gpu {st.gpu} was lost; worker exited with code "
                    f"{client.last_exit_code}; a fresh worker was started"
                )
            store.update(
                job_id,
                iteration=progress.iteration,
                iterations_run=progress.iterations_run,
                exploitability=progress.exploitability,
                exploitability_iteration=progress.exploitability_iteration,
                exploitability_history=progress.exploitability_history,
                elapsed_seconds=time.time() - started,
            )
            solve_elapsed = progress.elapsed_seconds or (time.monotonic() - solve_started)
            if progress.iterations_run and solve_elapsed > 0:
                st.iterations_per_second = round(progress.iterations_run / solve_elapsed, 2)
            if progress.state in ("done", "cancelled", "failed"):
                break
            if cancel_sent:
                continue
            if cancelled():
                client.cancel()
                cancel_sent = True
            elif target is not None:
                max_iterations, time_budget = time_cap
                if max_iterations is not None and progress.iterations_run >= max_iterations:
                    LOG.info("job %s hit max_iterations=%s; cancelling", job_id, max_iterations)
                    client.cancel()
                    cancel_sent = True
                elif time_budget is not None and time.time() - started >= time_budget:
                    LOG.info("job %s hit time_budget=%ss; cancelling", job_id, time_budget)
                    client.cancel()
                    cancel_sent = True

        # -- finish -----------------------------------------------------------
        if progress.state == "failed":
            if progress.error_code == protocol.ERROR_OOM:
                raise EngineOOMError(protocol.ERROR_OOM, progress.error or "CUDA out of memory")
            raise RemoteEngineError(protocol.ERROR_ENGINE, progress.error or "solve failed")

        result = client.wait(timeout=10)
        if result.iterations_run and result.elapsed_seconds:
            st.iterations_per_second = round(result.iterations_run / result.elapsed_seconds, 2)
        vram = result.vram or job.get("measured_vram") or {}
        peak = vram.get("peak_reserved_bytes")
        st.phase = "saving"
        strategy_path = self._export_strategy(job_id, client)
        fields = dict(
            elapsed_seconds=time.time() - started,
            iteration=result.total_iterations,
            iterations_run=result.iterations_run,
            exploitability=result.exploitability,
            exploitability_iteration=result.total_iterations if result.exploitability is not None else None,
            measured_vram=vram or None,
            peak_vram_bytes=peak,
            target_reached=result.target_reached,
            profile_value_p0=result.profile_value_p0,
            strategy_path=None if strategy_path is None else str(strategy_path),
        )
        timed_out = st.timeout_fired_at is not None and progress.state == "cancelled"
        if timed_out:
            store.finish(
                job_id, "failed", error_code="timeout",
                error=f"job exceeded timeout_seconds={st.timeout_seconds:g}; stopped at iteration "
                f"{result.total_iterations} (partial strategy kept)",
                **fields,
            )
        else:
            store.finish(job_id, progress.state, **fields)  # done or cancelled
        st.phase = "releasing"
        self._release(client)

    def _export_strategy(self, job_id: str, client: EngineClient) -> Path | None:
        try:
            strategy = client.get_strategy(flat=True)
        except (WorkerDiedError, EngineTimeoutError):
            raise
        except RemoteEngineError as exc:
            LOG.warning("job %s: strategy export failed: %s", job_id, exc)
            return None
        path = self._data_dir / ("strategy-%s.npz" % job_id)
        tmp = self._data_dir / ("strategy-%s.npz.tmp" % job_id)
        try:
            # Write-then-rename: a failed write never leaves a truncated .npz
            # behind under the final name.
            with self._open_for_write(str(tmp)) as handle:
                np.savez(
                    handle,
                    keys=np.array([key.encode("utf-8") for key in strategy.keys], dtype=np.bytes_),
                    actions=np.array(
                        [",".join(names).encode("utf-8") for names in strategy.actions], dtype=np.bytes_
                    ),
                    offsets=strategy.offsets,
                    values=strategy.values,
                    iteration=np.int64(strategy.iteration),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            # The solve itself finished; free the GPU before failing the job.
            self._release(client)
            if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                raise StrategySaveError(
                    "disk_full",
                    f"disk full writing the strategy to {self._data_dir} ({exc.strerror}); "
                    "the solve finished but its strategy was discarded; free space and resubmit",
                ) from exc
            raise StrategySaveError("storage_error", f"could not write the strategy: {exc}") from exc
        return path

    def _release(self, client: EngineClient) -> None:
        try:
            client.close()
        except WorkerDiedError:
            pass  # auto_restart already started a fresh worker
        except Exception:
            LOG.warning("worker close failed after job end", exc_info=True)
