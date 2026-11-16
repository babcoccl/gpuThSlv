"""Engine worker process: one per GPU, Python 3.9 runtime.

Serves a ``SolverEngine`` (or a ``FakeEngine``) over JSON-RPC on a Unix
socket; bulk arrays travel as ``.npy`` files under ``/dev/shm``. Run with
``python -m holdem_solver.engine_worker``. Importing this package does not
import torch or ``gpu_cfr``.
"""

from .fake import FakeEngine
from .service import EngineService, RpcError

__all__ = ["EngineService", "FakeEngine", "RpcError"]
