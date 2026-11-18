"""Import boundary (AGENTS.md): no code executed in the engine venv
(Python 3.9) may import ``holdem_solver.api``.

Everything reachable from the engine-venv roots —
``holdem_solver/engine_worker``, ``holdem_solver/protocol``, and every
script/test that imports ``gpu_cfr`` or ``engine_worker`` (directly or
transitively) — is walked as a static import graph; any edge into
``holdem_solver.api.*`` fails.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE_DIRS = (REPO / "holdem_solver" / "engine_worker",
               REPO / "holdem_solver" / "protocol")
FORBIDDEN_PREFIX = "holdem_solver.api"

# package root name -> directory containing it (for module resolution)
_PKG_DIRS = {
    "holdem_solver": REPO / "holdem_solver",
    "scripts": REPO / "scripts",
    "tests": REPO / "tests",
}
# module name -> file
_MODULE_FILES = {}
for pkg, base in _PKG_DIRS.items():
    for f in base.rglob("*.py"):
        rel = f.relative_to(base).with_suffix("")
        parts = [pkg] + [p for p in rel.parts if p != "__init__"]
        _MODULE_FILES[".".join(parts)] = f


def _imports(path):
    """Module names this file imports (absolute names where resolvable)."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return set()
    names = set()
    pkg_parts = []  # this file's own package, for relative imports
    for pkg, base in _PKG_DIRS.items():
        try:
            rel = path.relative_to(base).with_suffix("")
        except ValueError:
            continue
        pkg_parts = [pkg] + [p for p in rel.parts if p != "__init__"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base_name = node.module or ""
            else:
                # relative: resolve against this file's package
                up = pkg_parts[: len(pkg_parts) - node.level]
                base_name = ".".join(up + ([node.module] if node.module else []))
            names.add(base_name)
            # `from pkg import mod` may mean pkg.mod
            for a in node.names:
                names.add(base_name + "." + a.name)
    return names


def _is_api_module(name):
    return name == FORBIDDEN_PREFIX or name.startswith(FORBIDDEN_PREFIX + ".")


def test_engine_venv_never_imports_api():
    roots = [f for d in ENGINE_DIRS for f in sorted(d.glob("*.py"))]
    seeds = set()
    for f in list(REPO.glob("scripts/**/*.py")) + list(REPO.glob("tests/*.py")):
        imports = _imports(f)
        if any(n == "gpu_cfr" or n.startswith("gpu_cfr.")
               or n.startswith("holdem_solver.engine_worker")
               for n in imports):
            seeds.add(f)
    frontier = set(roots) | seeds
    seen = set()
    while frontier:
        f = frontier.pop()
        if f in seen:
            continue
        seen.add(f)
        for name in _imports(f):
            assert not _is_api_module(name), (
                "%s imports %s — engine-venv code must never import "
                "holdem_solver.api" % (f, name)
            )
            target = _MODULE_FILES.get(name)
            if target is not None and target not in seen:
                frontier.add(target)
    assert seen  # the walk actually covered files
