"""RPC method implementations around one engine instance.

All engine calls run on a single dedicated thread (CUDA graphs are captured
and replayed on the same thread); socket threads only read job state, so
``progress`` and ``cancel`` stay responsive while a solve is running.
"""

import dataclasses
import itertools
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from holdem_solver import protocol

_SOLVE_PARAMS = ("iterations", "time_budget", "target_exploitability", "progress_every", "chunk_iterations")
_MAX_WAIT_SECONDS = 60.0


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


def _engine_error(exc: BaseException) -> RpcError:
    code = protocol.ERROR_INVALID_PARAMS if isinstance(exc, (TypeError, ValueError)) else protocol.ERROR_ENGINE
    return RpcError(
        code,
        "%s: %s" % (type(exc).__name__, exc),
        {"type": type(exc).__name__, "traceback": traceback.format_exc()},
    )


class _Job:
    def __init__(self, job_id: int, params: Dict[str, Any], start_iteration: int) -> None:
        self.job_id = job_id
        self.params = params
        self.start_iteration = start_iteration
        self.started = time.time()
        self.finished: Optional[float] = None
        self.state = "solving"
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[RpcError] = None
        self.exploitability: Optional[float] = None
        self.exploitability_iteration: Optional[int] = None


class EngineService:
    def __init__(
        self,
        engine: Any,
        shm_dir: str,
        info: Optional[Dict[str, Any]] = None,
        vram_stats: Optional[Callable[[], Dict[str, int]]] = None,
        reset_peak_vram: Optional[Callable[[], None]] = None,
    ) -> None:
        self._engine = engine
        self._shm_dir = shm_dir
        self._info = dict(info or {})
        self._vram_stats = vram_stats or (lambda: {})
        self._reset_peak_vram = reset_peak_vram or (lambda: None)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="engine")
        # Held for the duration of any engine operation, including a whole solve job.
        self._engine_busy = threading.Lock()
        self._state_lock = threading.Lock()
        self._job: Optional[_Job] = None
        self._job_ids = itertools.count(1)
        self._file_ids = itertools.count(1)
        self._strategy_files: List[str] = []
        self._shutdown_requested = threading.Event()
        protocol.ensure_private_dir(self._shm_dir)
        self.clear_shm()

    # -- dispatch ---------------------------------------------------------

    def dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        handler = getattr(self, "rpc_" + method)
        try:
            return handler(**params)
        except RpcError:
            raise
        except TypeError as exc:
            raise RpcError(protocol.ERROR_INVALID_PARAMS, "bad params for %s: %s" % (method, exc))

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested.is_set()

    def _on_engine(self, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` on the engine thread, translating engine exceptions."""

        def call() -> Any:
            try:
                return fn()
            except RpcError:
                raise
            except Exception as exc:
                raise _engine_error(exc)

        return self._executor.submit(call).result()

    def _acquire_engine(self) -> None:
        if not self._engine_busy.acquire(blocking=False):
            raise RpcError(protocol.ERROR_BUSY, "engine is busy (a solve or build is running)")

    def _require_built(self) -> None:
        if not self._engine.is_built:
            raise RpcError(protocol.ERROR_NOT_BUILT, "engine is not built; call build first")

    # -- methods ----------------------------------------------------------

    def rpc_ping(self) -> Dict[str, Any]:
        result = dict(self._info)
        result.update(
            {
                "protocol_version": protocol.PROTOCOL_VERSION,
                "pid": os.getpid(),
                "built": bool(self._engine.is_built),
                "state": self._state(),
                "vram": self._vram_stats(),
            }
        )
        return result

    def rpc_build(self, game: Any, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not isinstance(game, (str, dict)):
            raise RpcError(protocol.ERROR_INVALID_PARAMS, "game must be a name or a spot object")
        options = dict(options or {})
        self._acquire_engine()
        try:
            start = time.perf_counter()

            def build() -> Dict[str, Any]:
                self._engine.close()
                self._reset_peak_vram()
                self._engine.build(game, **options)
                return self._engine.describe()

            with self._state_lock:
                self._job = None
            result = self._on_engine(build)
            result["build_seconds"] = time.perf_counter() - start
            result["vram"] = self._vram_stats()
            return result
        finally:
            self._engine_busy.release()

    def rpc_solve(self, **params: Any) -> Dict[str, Any]:
        unknown = sorted(set(params) - set(_SOLVE_PARAMS))
        if unknown:
            raise RpcError(protocol.ERROR_INVALID_PARAMS, "unknown solve params: %s" % ", ".join(unknown))
        self._acquire_engine()
        try:
            self._require_built()
            job = _Job(next(self._job_ids), dict(params), int(self._engine.iterations))
            with self._state_lock:
                self._job = job
            self._executor.submit(self._run_job, job)
        except BaseException:
            self._engine_busy.release()
            raise
        return {"job_id": job.job_id, "state": job.state}

    def _run_job(self, job: _Job) -> None:
        def on_progress(snapshot: Any) -> None:
            job.exploitability = float(snapshot.exploitability)
            job.exploitability_iteration = int(snapshot.iteration)

        kwargs = dict(job.params)
        if kwargs.get("progress_every") is not None:
            kwargs["progress_callback"] = on_progress
        try:
            result = self._engine.solve(should_stop=job.cancel.is_set, **kwargs)
            job.result = dataclasses.asdict(result)
            job.result["vram"] = self._vram_stats()
            job.exploitability = float(result.exploitability)
            job.exploitability_iteration = int(result.total_iterations)
            job.state = "cancelled" if result.cancelled else "done"
        except Exception as exc:
            job.error = _engine_error(exc)
            job.state = "failed"
        finally:
            job.finished = time.time()
            self._engine_busy.release()
            job.done.set()

    def rpc_solve_wait(self, timeout: Optional[float] = None, job_id: Optional[int] = None) -> Dict[str, Any]:
        job = self._job
        if job is None:
            raise RpcError(protocol.ERROR_NO_JOB, "no solve has been started")
        if job_id is not None and job.job_id != job_id:
            raise RpcError(protocol.ERROR_NO_JOB, "solve job %s was replaced by job %d" % (job_id, job.job_id))
        wait = _MAX_WAIT_SECONDS if timeout is None else max(0.0, min(float(timeout), _MAX_WAIT_SECONDS))
        if not job.done.wait(wait):
            return {"done": False, "job_id": job.job_id, "state": job.state}
        if job.error is not None:
            raise job.error
        return {"done": True, "job_id": job.job_id, "state": job.state, "result": job.result}

    def _state(self) -> str:
        job = self._job
        if job is not None:
            return job.state
        return "idle" if self._engine.is_built else "unbuilt"

    def rpc_progress(self) -> Dict[str, Any]:
        job = self._job
        iteration = int(self._engine.iterations)
        if job is None:
            return {"state": self._state(), "job_id": None, "iteration": iteration, "iterations_run": 0}
        end = job.finished if job.finished is not None else time.time()
        return {
            "state": job.state,
            "job_id": job.job_id,
            "iteration": iteration,
            "iterations_run": iteration - job.start_iteration,
            "target_iterations": job.params.get("iterations"),
            "time_budget": job.params.get("time_budget"),
            "target_exploitability": job.params.get("target_exploitability"),
            "elapsed_seconds": end - job.started,
            "exploitability": job.exploitability,
            "exploitability_iteration": job.exploitability_iteration,
            "cancel_requested": job.cancel.is_set(),
            "error": None if job.error is None else job.error.message,
        }

    def rpc_cancel(self) -> Dict[str, Any]:
        job = self._job
        if job is None or job.done.is_set():
            return {"cancel_requested": False, "state": self._state()}
        job.cancel.set()
        return {"cancel_requested": True, "state": job.state}

    def rpc_get_strategy(self) -> Dict[str, Any]:
        self._acquire_engine()
        try:
            self._require_built()
            strategy = self._on_engine(self._engine.get_strategy)
            iteration = int(self._engine.iterations)
        finally:
            self._engine_busy.release()

        keys = list(strategy)
        rows = [np.asarray(strategy[key]).reshape(-1) for key in keys]
        offsets = np.zeros(len(rows) + 1, dtype=np.int64)
        if rows:
            np.cumsum([row.size for row in rows], out=offsets[1:])
            values = np.concatenate(rows)
        else:
            values = np.zeros(0, dtype=np.float32)
        key_bytes = np.array([key.encode("utf-8") for key in keys], dtype=np.bytes_)

        # A strategy the client never collected is replaced, not accumulated.
        self._remove_files(self._strategy_files)
        token = "%d-%d" % (os.getpid(), next(self._file_ids))
        refs = {
            "keys": self._save("strategy-keys-%s.npy" % token, key_bytes),
            "offsets": self._save("strategy-offsets-%s.npy" % token, offsets),
            "values": self._save("strategy-values-%s.npy" % token, values),
        }
        self._strategy_files = [ref[protocol.ARRAY_REF_KEY] for ref in refs.values()]
        refs.update({"num_infosets": len(keys), "iteration": iteration})
        return refs

    def rpc_reset(self) -> Dict[str, Any]:
        self._acquire_engine()
        try:
            self._require_built()
            self._on_engine(self._engine.reset)
            with self._state_lock:
                self._job = None
            return {"iteration": int(self._engine.iterations)}
        finally:
            self._engine_busy.release()

    def rpc_close(self) -> Dict[str, Any]:
        """Release the solver (cancelling a running solve); the worker stays up."""
        self._release_engine()
        return {"state": self._state(), "vram": self._vram_stats()}

    def rpc_shutdown(self) -> Dict[str, Any]:
        self._shutdown_requested.set()
        return {"pid": os.getpid()}

    # -- lifecycle --------------------------------------------------------

    def _release_engine(self) -> None:
        # Re-cancel while waiting: a solve can be admitted after the first look.
        while True:
            job = self._job
            if job is not None:
                job.cancel.set()
            if self._engine_busy.acquire(timeout=0.05):
                break
        try:
            self._on_engine(self._engine.close)
            with self._state_lock:
                self._job = None
        finally:
            self._engine_busy.release()
        self._remove_files(self._strategy_files)
        self._strategy_files = []

    def stop(self) -> None:
        """Cancel work, release the engine, and remove this worker's shm files."""
        try:
            self._release_engine()
        except Exception:
            pass
        self._executor.shutdown(wait=True)
        self.clear_shm()

    # -- /dev/shm ---------------------------------------------------------

    def _save(self, name: str, array: Any) -> Dict[str, Any]:
        path = os.path.join(self._shm_dir, name)
        with open(path, "wb") as handle:
            np.save(handle, array, allow_pickle=False)
        return protocol.array_ref(path, array.shape, str(array.dtype))

    @staticmethod
    def _remove_files(paths: List[str]) -> None:
        for path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass

    def clear_shm(self) -> None:
        try:
            names = os.listdir(self._shm_dir)
        except OSError:
            return
        self._remove_files([os.path.join(self._shm_dir, name) for name in names if name.endswith(".npy")])
