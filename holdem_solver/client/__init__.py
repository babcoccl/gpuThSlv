"""API-side (Python 3.15) client for the engine worker processes.

Imports only the standard library, numpy, and ``holdem_solver.protocol``;
never ``holdem_solver.engine``, ``holdem_solver.engine_worker``, torch, or
``gpu_cfr``.
"""

from .engine_client import BuildInfo, EngineClient, Progress, SolveResult, Strategy
from .errors import (
    EngineBusyError,
    EngineError,
    EngineTimeoutError,
    NotBuiltError,
    RemoteEngineError,
    WorkerDiedError,
    WorkerStartError,
)
from .process import WorkerProcess
from holdem_solver.protocol import BET_SIZE_SETS, SpotConfig

__all__ = [
    "BET_SIZE_SETS",
    "SpotConfig",
    "BuildInfo",
    "EngineBusyError",
    "EngineClient",
    "EngineError",
    "EngineTimeoutError",
    "NotBuiltError",
    "Progress",
    "RemoteEngineError",
    "SolveResult",
    "Strategy",
    "WorkerDiedError",
    "WorkerProcess",
    "WorkerStartError",
]
