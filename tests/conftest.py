"""Each runtime collects only the tests it can import.

``.venv-engine`` (Python 3.9) runs the engine and worker tests;
``.venv-api`` (Python 3.15) runs the client tests. Protocol and worker-service
tests run in both.
"""

import sys

if sys.version_info < (3, 10):
    collect_ignore_glob = ["test_client*.py", "test_api*.py"]
else:
    collect_ignore_glob = ["test_solver_engine.py", "test_spot_builder.py", "test_phase6_raises.py"]
