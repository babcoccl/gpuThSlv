"""``EngineClient``: drive one engine worker over its Unix socket."""

from __future__ import annotations

import itertools
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np

from holdem_solver import protocol

from .errors import EngineTimeoutError, WorkerDiedError, WorkerStartError, remote_error
from .process import WorkerProcess

_WAIT_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class BuildInfo:
    game: str
    num_nodes: int
    num_infosets: int
    num_infoset_actions: int
    estimated_vram_bytes: int
    device: str | None
    build_seconds: float
    vram: dict[str, int] = field(default_factory=dict)
    spot: dict[str, Any] | None = None


@dataclass(frozen=True)
class SolveResult:
    iterations_run: int
    total_iterations: int
    elapsed_seconds: float
    exploitability: float
    profile_value_p0: float
    target_exploitability: float | None
    target_reached: bool
    time_budget_exhausted: bool
    cancelled: bool = False
    vram: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Progress:
    state: str
    job_id: int | None
    iteration: int
    iterations_run: int
    target_iterations: int | None = None
    time_budget: float | None = None
    target_exploitability: float | None = None
    elapsed_seconds: float = 0.0
    exploitability: float | None = None
    exploitability_iteration: int | None = None
    exploitability_history: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
    error: str | None = None
    error_code: int | None = None


@dataclass(frozen=True)
class Strategy:
    """Average strategy in flat form: row ``i`` is ``values[offsets[i]:offsets[i + 1]]``."""

    keys: list[str]
    offsets: np.ndarray
    values: np.ndarray
    iteration: int
    actions: list[list[str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, np.ndarray]:
        bounds = self.offsets.tolist()
        return {key: self.values[bounds[i] : bounds[i + 1]] for i, key in enumerate(self.keys)}


def _from_wire(cls: type, payload: dict[str, Any]) -> Any:
    names = {item.name for item in fields(cls)}
    return cls(**{key: value for key, value in payload.items() if key in names})


class EngineClient:
    """Client for one worker (one GPU).

    By default the client spawns and owns the worker; pass ``spawn=False`` to
    attach to one started elsewhere. Each call opens its own connection, so
    ``progress()`` and ``cancel()`` may be called from another thread while
    ``solve()`` blocks.

    ``close()`` releases the solver on the worker, like ``SolverEngine.close``;
    ``shutdown()`` stops the worker process. Leaving a ``with`` block shuts
    down a worker this client spawned.

    If the worker dies, the call that notices raises ``WorkerDiedError``.
    With ``auto_restart=True`` a fresh worker is started before raising;
    otherwise call ``restart()``. Either way the new worker is unbuilt.
    """

    def __init__(
        self,
        gpu: int | None = 0,
        *,
        engine: str = "real",
        spawn: bool = True,
        auto_restart: bool = False,
        python: str | None = None,
        socket_path: str | None = None,
        shm_dir: str | None = None,
        log_path: str | None = None,
        start_timeout: float = 120.0,
        rpc_timeout: float = 30.0,
        build_timeout: float = 1800.0,
    ) -> None:
        self.gpu = gpu
        self.auto_restart = auto_restart
        self.start_timeout = start_timeout
        self.rpc_timeout = rpc_timeout
        self.build_timeout = build_timeout
        self._worker = WorkerProcess(
            gpu, engine=engine, python=python, socket_path=socket_path, shm_dir=shm_dir, log_path=log_path
        )
        self._owns_worker = spawn
        self._ids = itertools.count(1)
        self._restart_lock = threading.Lock()
        self._job_id: int | None = None
        self._generation = 0
        self._strategy_lock = threading.Lock()
        if spawn:
            self.start()

    # -- worker lifecycle -------------------------------------------------

    @property
    def socket_path(self) -> str:
        return self._worker.socket_path

    @property
    def shm_dir(self) -> str:
        return self._worker.shm_dir

    @property
    def log_path(self) -> str:
        return self._worker.log_path

    @property
    def worker_pid(self) -> int | None:
        """PID of the worker this client spawned (``None`` when attached)."""
        return self._worker.pid

    def is_alive(self) -> bool:
        if self._owns_worker:
            return self._worker.is_alive()
        try:
            self.ping()
        except (WorkerDiedError, EngineTimeoutError):
            return False
        return True

    def start(self) -> dict[str, Any]:
        """Spawn the worker if needed and wait until it answers ``ping``."""
        if not self._owns_worker:
            return self.ping()
        self._worker.spawn()
        deadline = time.monotonic() + self.start_timeout
        while True:
            if not self._worker.is_alive():
                raise WorkerStartError(
                    f"worker exited with code {self._worker.returncode()} during startup; "
                    f"log {self._worker.log_path}:\n{self._worker.log_tail()}"
                )
            try:
                info = self._call("ping", timeout=5.0, restart_on_death=False)
            except (WorkerDiedError, EngineTimeoutError):
                if time.monotonic() >= deadline:
                    self._worker.stop()
                    raise WorkerStartError(
                        f"worker not ready after {self.start_timeout:.0f}s; log {self._worker.log_path}"
                    ) from None
                time.sleep(0.05)
                continue
            if info.get("pid") != self._worker.pid:
                self._worker.stop()
                raise WorkerStartError(
                    f"socket {self.socket_path} is served by pid {info.get('pid')}, not the worker just spawned"
                )
            if info.get("protocol_version") != protocol.PROTOCOL_VERSION:
                self._worker.stop()
                raise WorkerStartError(
                    f"worker speaks protocol {info.get('protocol_version')}, client {protocol.PROTOCOL_VERSION}"
                )
            return info

    def restart(self) -> dict[str, Any]:
        """Stop the worker (if still running) and start a fresh, unbuilt one."""
        if not self._owns_worker:
            raise RuntimeError("this client did not spawn the worker; restart it where it was started")
        with self._restart_lock:
            return self._restart_locked()

    def _restart_locked(self) -> dict[str, Any]:
        self._worker.stop()
        self._job_id = None
        info = self.start()
        # Bumped only once the new worker answers: a call that failed while it
        # was coming up must not restart it again.
        self._generation += 1
        return info

    def kill_and_restart(self) -> dict[str, Any]:
        """SIGKILL the worker and start a fresh one (hung-worker recovery).

        Holds the restart lock throughout, so calls that fail because of the
        kill see a new generation and do not restart a second time.
        """
        if not self._owns_worker:
            raise RuntimeError("this client did not spawn the worker; restart it where it was started")
        with self._restart_lock:
            self._worker.kill()
            return self._restart_locked()

    def shutdown(self, timeout: float = 10.0) -> None:
        """Stop the worker process. Only for a worker this client spawned."""
        if not self._owns_worker:
            return
        if self._worker.is_alive():
            try:
                self._call("shutdown", timeout=5.0, restart_on_death=False)
            except (WorkerDiedError, EngineTimeoutError):
                pass
            self._worker.wait_exit(timeout)
        self._worker.stop()

    def __enter__(self) -> EngineClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    # -- transport --------------------------------------------------------

    def _call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        restart_on_death: bool = True,
    ) -> Any:
        request_id = next(self._ids)
        generation = self._generation
        payload = protocol.encode_message(protocol.make_request(method, params, request_id))
        timeout = self.rpc_timeout if timeout is None else timeout
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(self.socket_path)
                sock.sendall(payload)
                with sock.makefile("rb") as stream:
                    message = protocol.read_message(stream)
        except TimeoutError as exc:
            raise EngineTimeoutError(f"{method}: no answer from worker within {timeout:.1f}s") from exc
        except (OSError, protocol.ProtocolError) as exc:
            raise self._died(method, exc, restart_on_death, generation) from exc
        if message is None:
            raise self._died(method, None, restart_on_death, generation)
        response_id, result, error = protocol.parse_response(message)
        if error is not None:
            raise remote_error(error)
        if response_id != request_id:
            raise protocol.ProtocolError(protocol.ERROR_INVALID_REQUEST, "response id does not match request")
        return result

    def _died(self, method: str, cause: BaseException | None, restart: bool, generation: int) -> WorkerDiedError:
        detail = "connection closed" if cause is None else f"{type(cause).__name__}: {cause}"
        message = f"{method}: engine worker for gpu {self.gpu} is not reachable ({detail})"
        if self._owns_worker:
            code = self._worker.returncode()
            if code is not None:
                message += f"; worker exited with code {code}"
            if restart and self.auto_restart:
                try:
                    # Several calls can see the same death; only the first restarts.
                    with self._restart_lock:
                        if self._generation == generation:
                            self._restart_locked()
                    message += "; a fresh worker was started and must be rebuilt"
                except WorkerStartError as exc:
                    message += f"; restart failed: {exc}"
        return WorkerDiedError(message)

    # -- engine API -------------------------------------------------------

    def ping(self, timeout: float | None = None) -> dict[str, Any]:
        """Worker facts: pid, gpu, python/torch versions, device name, state, VRAM."""
        return self._call("ping", timeout=timeout)

    def build(self, game: str | dict[str, Any] | protocol.SpotConfig, **options: Any) -> BuildInfo:
        """Build ``game`` on the worker, replacing any existing solver.

        ``game`` is a registry name (``"kuhn_poker"``) or a river/turn spot: a
        ``SpotConfig`` or its dict form, validated here before it is sent.
        """
        if not isinstance(game, str):
            game = protocol.as_spot_dict(game)
        result = self._call("build", {"game": game, "options": options}, timeout=self.build_timeout)
        return _from_wire(BuildInfo, result)

    def estimate_spot(
        self, spot: dict[str, Any] | protocol.SpotConfig, dtype: str = "float32"
    ) -> dict[str, Any]:
        """Node count and estimated VRAM for a spot at ``dtype`` precision, without building it."""
        return self._call("estimate_spot", {"spot": protocol.as_spot_dict(spot), "dtype": dtype})

    def solve(
        self,
        iterations: int | None = None,
        time_budget: float | None = None,
        target_exploitability: float | None = None,
        *,
        progress_every: int | None = None,
        chunk_iterations: int | None = None,
        wait: bool = True,
        timeout: float | None = None,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> SolveResult | None:
        """Start a solve; with ``wait`` (default) block and return its result.

        Same modes as ``SolverEngine.solve``. ``progress_every`` makes the
        worker evaluate exploitability every N iterations, visible through
        ``progress()``. ``on_progress`` is called with a ``Progress`` snapshot
        about every 2 seconds while waiting. With ``wait=False`` this returns
        ``None`` immediately; use ``progress()``, ``cancel()``, and ``wait()``.
        """
        params: dict[str, Any] = {
            "iterations": iterations,
            "time_budget": time_budget,
            "target_exploitability": target_exploitability,
            "progress_every": progress_every,
            "chunk_iterations": chunk_iterations,
        }
        job_id = self._call("solve", {key: value for key, value in params.items() if value is not None})["job_id"]
        self._job_id = job_id
        if not wait:
            return None
        return self._wait(job_id, timeout, on_progress)

    def wait(
        self, timeout: float | None = None, on_progress: Callable[[Progress], None] | None = None
    ) -> SolveResult:
        """Block until the current solve ends (finished or cancelled).

        Waits for the solve this client started last (or, for a client that
        started none, whatever solve the worker has). Raises
        ``EngineTimeoutError`` after ``timeout`` seconds; the solve keeps
        running and can still be cancelled or waited on again.
        """
        return self._wait(self._job_id, timeout, on_progress)

    def _wait(
        self, job_id: int | None, timeout: float | None, on_progress: Callable[[Progress], None] | None
    ) -> SolveResult:
        params: dict[str, Any] = {} if job_id is None else {"job_id": job_id}
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            poll = _WAIT_POLL_SECONDS
            if deadline is not None:
                poll = min(poll, max(0.0, deadline - time.monotonic()))
            answer = self._call("solve_wait", dict(params, timeout=poll), timeout=poll + self.rpc_timeout)
            if answer["done"]:
                return _from_wire(SolveResult, answer["result"])
            if on_progress is not None:
                on_progress(self.progress())
            if deadline is not None and time.monotonic() >= deadline:
                raise EngineTimeoutError(f"solve still running after {timeout:.1f}s")

    def progress(self) -> Progress:
        return _from_wire(Progress, self._call("progress"))

    def cancel(self, timeout: float | None = None) -> bool:
        """Ask a running solve to stop at its next chunk; true if one was running."""
        return bool(self._call("cancel", timeout=timeout)["cancel_requested"])

    def get_strategy(self, *, flat: bool = False) -> dict[str, np.ndarray] | Strategy:
        """Average strategy keyed by infoset; ``flat=True`` skips the dict."""
        # The worker replaces uncollected strategy files on the next export.
        with self._strategy_lock:
            answer = self._call("get_strategy")
            keys = self._take_array(answer["keys"])
            actions = self._take_array(answer["actions"]) if "actions" in answer else np.array([])
            strategy = Strategy(
                keys=[key.decode("utf-8") for key in keys.tolist()],
                offsets=self._take_array(answer["offsets"]),
                values=self._take_array(answer["values"]),
                iteration=int(answer["iteration"]),
                actions=[
                    [name for name in row.decode("utf-8").split(",") if name] for row in actions.tolist()
                ],
            )
        return strategy if flat else strategy.as_dict()

    def reset(self) -> None:
        """Zero the accumulators and iteration count, keeping the built game."""
        self._call("reset")

    def close(self) -> None:
        """Release the solver and its VRAM on the worker; the worker stays up."""
        self._call("close", timeout=max(self.rpc_timeout, 120.0))

    def _take_array(self, ref: dict[str, Any]) -> np.ndarray:
        """Load a worker-written .npy from /dev/shm and delete the file."""
        path = protocol.resolve_array_ref(ref, self.shm_dir)
        try:
            array = np.load(path, allow_pickle=False)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if list(array.shape) != ref["shape"] or str(array.dtype) != ref["dtype"]:
            raise protocol.ProtocolError(protocol.ERROR_INVALID_REQUEST, f"array {path} does not match its reference")
        return array
