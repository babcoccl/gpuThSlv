"""Job scheduler: one job at a time per GPU, least-loaded assignment.

A dispatcher thread hands each queued job to the idle GPU with the fewest
completed jobs (ties broken by GPU index). A runner thread per job builds the
spot, starts the solve asynchronously, polls ``progress()`` to keep the job
row fresh, enforces the ``max_iterations`` / ``time_budget`` caps the API adds
to ``target_exploitability`` mode, then exports the strategy to
``<data_dir>/strategy-<id>.npz`` and releases the solver so the GPU is free
for the next job.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from holdem_solver.client import EngineClient, RemoteEngineError, WorkerDiedError

from .store import JobStore

LOG = logging.getLogger("holdem_solver.api")

_POLL_SECONDS = 1.0


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
    ) -> None:
        self._store = store
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._engine = engine
        self._client_kwargs = client_kwargs or {}
        self._client_factory = client_factory
        self._clients: dict[int | None, EngineClient] = {}
        self._busy: set[int | None] = set()
        self._completed: dict[int | None, int] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="scheduler", daemon=True)
        self._runners: list[threading.Thread] = []
        self._gpus = list(gpus)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        for gpu in self._gpus:
            self._clients[gpu] = self._client_factory(
                gpu, engine=self._engine, auto_restart=False, **self._client_kwargs.get(gpu, {})
            )
            self._completed[gpu] = 0
        self._dispatcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._dispatcher.join(timeout=5)
        for thread in self._runners:
            thread.join(timeout=5)
        for client in self._clients.values():
            try:
                client.shutdown()
            except Exception:
                LOG.warning("worker shutdown failed", exc_info=True)

    # -- public surface used by the app -------------------------------------

    def wake(self) -> None:
        self._wake.set()

    def client(self, gpu: int | None) -> EngineClient | None:
        return self._clients.get(gpu)

    @property
    def gpus(self) -> list[int | None]:
        return list(self._gpus)

    def gpu_status(self, gpu: int | None) -> dict[str, Any]:
        """Worker ping + running job for one GPU."""
        client = self._clients.get(gpu)
        info: dict[str, Any] = {"gpu": gpu, "state": "unavailable"}
        if client is not None:
            try:
                ping = client.ping()
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
        running = self._store.running_on(gpu)
        info["job_id"] = None if running is None else running["id"]
        return info

    def estimate(self, spot: dict[str, Any]) -> dict[str, Any]:
        """Node count + VRAM estimate for a spot, from any live worker."""
        last_error: Exception | None = None
        for gpu in self._gpus:
            client = self._clients.get(gpu)
            if client is None:
                continue
            try:
                return client.estimate_spot(spot)
            except RemoteEngineError:
                raise
            except Exception as exc:  # worker down — try the next GPU
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("no engine workers are running")

    # -- dispatch ------------------------------------------------------------

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            job = self._store.next_queued()
            if job is not None:
                gpu = self._idle_gpu()
                if gpu is not None and self._store.claim(job["id"], gpu):
                    with self._lock:
                        self._busy.add(gpu)
                    thread = threading.Thread(
                        target=self._run, args=(job["id"], gpu), name="job-%s" % job["id"], daemon=True
                    )
                    self._runners.append(thread)
                    thread.start()
                    continue
            self._wake.wait(timeout=_POLL_SECONDS)
            self._wake.clear()

    def _idle_gpu(self) -> int | None:
        """Least-loaded idle GPU (fewest finished jobs, lowest index on ties)."""
        with self._lock:
            idle = [gpu for gpu in self._gpus if gpu not in self._busy]
        if not idle:
            return None
        return min(idle, key=lambda gpu: self._completed.get(gpu, 0))

    # -- runner ---------------------------------------------------------------

    def _run(self, job_id: str, gpu: int | None) -> None:
        client = self._clients[gpu]
        try:
            self._run_job(job_id, client)
        except Exception:
            LOG.exception("job %s failed", job_id)
            self._store.update(
                job_id,
                status="failed",
                finished_at=time.time(),
                error="internal error running the job; see api logs",
            )
        finally:
            with self._lock:
                self._busy.discard(gpu)
                self._completed[gpu] = self._completed.get(gpu, 0) + 1
            self._wake.set()

    def _run_job(self, job_id: str, client: EngineClient) -> None:
        store = self._store
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
            store.update(job_id, status="cancelled", finished_at=time.time())
            return
        solver_options = dict(params.pop("solver_options", {}) or {})
        solver_options["variant"] = params.pop("variant", "cfr_plus")
        built = client.build(spot, **solver_options)
        store.update(
            job_id,
            status="solving",
            num_nodes=built.num_nodes,
            num_infosets=built.num_infosets,
            estimated_vram_bytes=built.estimated_vram_bytes,
            measured_vram=built.vram,
            build_seconds=built.build_seconds,
        )

        # -- solve ----------------------------------------------------------
        target = params.get("target_exploitability")
        time_cap = params.pop("max_iterations", None), params.get("time_budget")
        solve_kwargs = dict(params)
        solve_kwargs.pop("max_iterations", None)
        if target is not None:
            # The engine's target mode has no caps; the API's are enforced here.
            solve_kwargs.pop("time_budget", None)
        client.solve(wait=False, **solve_kwargs)

        while True:
            time.sleep(_POLL_SECONDS)
            progress = client.progress()
            store.update(
                job_id,
                iteration=progress.iteration,
                iterations_run=progress.iterations_run,
                exploitability=progress.exploitability,
                exploitability_history=progress.exploitability_history,
                elapsed_seconds=time.time() - started,
            )
            if progress.state in ("done", "cancelled", "failed"):
                break
            if cancelled():
                client.cancel()
                continue
            if target is not None:
                max_iterations, time_budget = time_cap
                if max_iterations is not None and progress.iterations_run >= max_iterations:
                    LOG.info("job %s hit max_iterations=%s; cancelling", job_id, max_iterations)
                    client.cancel()
                elif time_budget is not None and time.time() - started >= time_budget:
                    LOG.info("job %s hit time_budget=%ss; cancelling", job_id, time_budget)
                    client.cancel()

        # -- finish -----------------------------------------------------------
        if progress.state == "failed":
            store.update(
                job_id,
                status="failed",
                finished_at=time.time(),
                error=progress.error or "solve failed",
            )
            self._release(client)
            return

        result = client.wait(timeout=10)
        strategy_path = self._export_strategy(job_id, client)
        store.update(
            job_id,
            status=progress.state,  # done or cancelled
            finished_at=time.time(),
            elapsed_seconds=time.time() - started,
            iteration=result.total_iterations,
            iterations_run=result.iterations_run,
            exploitability=result.exploitability,
            measured_vram=result.vram or job.get("measured_vram"),
            target_reached=result.target_reached,
            profile_value_p0=result.profile_value_p0,
            strategy_path=None if strategy_path is None else str(strategy_path),
        )
        self._release(client)

    def _export_strategy(self, job_id: str, client: EngineClient) -> Path | None:
        try:
            strategy = client.get_strategy(flat=True)
        except RemoteEngineError as exc:
            LOG.warning("job %s: strategy export failed: %s", job_id, exc)
            return None
        path = self._data_dir / ("strategy-%s.npz" % job_id)
        np.savez(
            path,
            keys=np.array([key.encode("utf-8") for key in strategy.keys], dtype=np.bytes_),
            actions=np.array(
                [",".join(names).encode("utf-8") for names in strategy.actions], dtype=np.bytes_
            ),
            offsets=strategy.offsets,
            values=strategy.values,
            iteration=np.int64(strategy.iteration),
        )
        return path

    def _release(self, client: EngineClient) -> None:
        try:
            client.close()
        except WorkerDiedError:
            try:
                client.restart()
            except Exception:
                LOG.warning("worker restart failed after job end", exc_info=True)
        except Exception:
            LOG.warning("worker close failed after job end", exc_info=True)
