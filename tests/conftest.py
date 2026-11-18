"""Runs in ``.venv-engine`` and ``.venv-api``: each runtime collects only
the tests it can import.

``.venv-engine`` (Python 3.9) runs the engine and worker tests;
``.venv-api`` (Python 3.15) runs the client tests. Protocol and worker-service
tests run in both.
"""

import sys

if sys.version_info < (3, 10):
    collect_ignore_glob = ["test_client*.py", "test_api*.py"]
else:
    collect_ignore_glob = ["test_solver_engine.py", "test_spot_builder.py", "test_phase6_raises.py"]

# Runtime backstop for the import boundary (AGENTS.md): nothing running in
# the engine venv may import holdem_solver.api. The static walk in
# tests/test_import_boundary.py is the primary guard; this hook makes an
# accidental engine-side import fail at runtime too — e.g. inside a
# function the static walk was tricked into missing.
if sys.version_info < (3, 10):
    import importlib.abc

    class _ApiBoundaryBlocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "holdem_solver.api" or name.startswith("holdem_solver.api."):
                raise ImportError(
                    "holdem_solver.api is forbidden in the engine venv "
                    "(import boundary, AGENTS.md)"
                )
            return None

    sys.meta_path.insert(0, _ApiBoundaryBlocker())
