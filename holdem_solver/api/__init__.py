"""REST API for the solver: Python 3.15 runtime (``.venv-api``) only.

Wraps :class:`holdem_solver.client.EngineClient`; never imports torch,
``gpu_cfr``, ``holdem_solver.engine_worker``, or the engine solver code.
Run with ``python -m holdem_solver.api``.
"""

from .app import create_app

__all__ = ["create_app"]
