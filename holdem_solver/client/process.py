"""Spawn and supervise one engine worker process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from holdem_solver import protocol

from .errors import WorkerStartError

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_worker_python(engine: str) -> str:
    """The Python 3.9 engine venv for the real engine; this interpreter for the fake."""
    override = os.environ.get("HOLDEM_SOLVER_ENGINE_PYTHON")
    if override:
        return override
    if engine == "fake":
        return sys.executable
    return str(REPO_ROOT / ".venv-engine" / "bin" / "python")


class WorkerProcess:
    """One worker, pinned to one GPU through ``CUDA_VISIBLE_DEVICES``."""

    def __init__(
        self,
        gpu: int | None,
        *,
        engine: str = "real",
        python: str | None = None,
        socket_path: str | None = None,
        shm_dir: str | None = None,
        log_path: str | None = None,
    ) -> None:
        if engine not in ("real", "fake"):
            raise ValueError("engine must be 'real' or 'fake'")
        self.gpu = gpu
        self.engine = engine
        self.python = python or default_worker_python(engine)
        self.socket_path = socket_path or protocol.socket_path(gpu)
        self.shm_dir = shm_dir or protocol.shm_dir(gpu)
        self.log_path = log_path or self.socket_path.removesuffix(".sock") + ".log"
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def returncode(self) -> int | None:
        return None if self._process is None else self._process.poll()

    def log_tail(self, lines: int = 20) -> str:
        try:
            with open(self.log_path, errors="replace") as handle:
                return "".join(handle.readlines()[-lines:])
        except OSError:
            return ""

    def spawn(self) -> None:
        """Start the process; readiness is checked by the caller with a ping."""
        with self._lock:
            if self.is_alive():
                return
            protocol.ensure_private_dir(os.path.dirname(self.socket_path))
            command = [
                self.python,
                "-m",
                "holdem_solver.engine_worker",
                "--engine",
                self.engine,
                "--socket",
                self.socket_path,
                "--shm-dir",
                self.shm_dir,
                "--parent-pid",
                str(os.getpid()),
            ]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env.pop("PYTHONHOME", None)
            env.pop("VIRTUAL_ENV", None)
            if self.gpu is not None:
                command += ["--gpu", str(self.gpu)]
                env["CUDA_VISIBLE_DEVICES"] = str(self.gpu)
            try:
                with open(self.log_path, "ab") as log:
                    self._process = subprocess.Popen(
                        command,
                        cwd=str(REPO_ROOT),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
            except OSError as exc:
                raise WorkerStartError(f"cannot start worker with {self.python}: {exc}") from exc

    def stop(self, timeout: float = 10.0) -> None:
        """SIGTERM, then SIGKILL after ``timeout`` seconds."""
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self._process = None

    def kill(self) -> None:
        """SIGKILL: a hung worker may not handle SIGTERM (e.g. stopped or stuck in a CUDA call)."""
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                process.kill()
            process.wait()
            self._process = None

    def wait_exit(self, timeout: float) -> bool:
        process = self._process
        if process is None:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(0.02)
        return process.poll() is not None
