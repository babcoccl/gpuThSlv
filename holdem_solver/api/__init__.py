"""REST API for the solver: Python 3.15 runtime (``.venv-api``) only.

Wraps :class:`holdem_solver.client.EngineClient`; never imports torch,
``gpu_cfr``, ``holdem_solver.engine_worker``, or the engine solver code.
Run with ``python -m holdem_solver.api``.

``create_app`` is resolved lazily: the evaluation helpers
(``api.evec``, ``api.nodes``, ``api.strategy``) are importable from the
engine venv too (they need only numpy + the shared protocol package).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import create_app

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(name)
