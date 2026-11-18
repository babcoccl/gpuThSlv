"""Import boundary (AGENTS.md): no code executed in the engine venv
(Python 3.9) may import ``holdem_solver.api``.

Fail-closed: every ``scripts/**/*.py`` and ``tests/**/*.py` file declares
its runtime venv(s) in its docstring (``.venv-engine`` and/or
``.venv-api``) and this test fails on undeclared files.

Engine seeds = every ``engine_worker/**`` and ``protocol/**`` module, plus
every scripts/tests file declaring ``.venv-engine``, plus every
scripts/tests file importing ``gpu_cfr`` or ``holdem_solver.engine_worker``
at any level. Their transitive import closure (``holdem_solver.*``,
``scripts.*``, ``tests.*``; module- and function-level) must never reach
``holdem_solver.api``. A runtime ``sys.meta_path`` hook in
``tests/conftest.py`` raises on the same import during engine-venv runs.

Resolution mirrors what the interpreter does for this repo: repo package
roots first, then — like pytest's rootdir/conftest handling — bare
sibling imports (``from evs_fixture import ...``) resolve against the
importing file's own directory, plus any ``sys.path`` entries configured
by ``conftest.py``/``pytest.ini`` (``pythonpath``). Every import is
classified stdlib / installed third-party / local; an unresolved import
fails the suite.

Runs in ``.venv-engine`` and ``.venv-api`` (pure ``ast``, no imports of the
checked code).
"""
import ast
import importlib.util
import os
import re
import sys
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


def build_module_files(roots=None):
    """module name -> file for every ``*.py`` under ``roots`` — the
    indexer the boundary checks use (also what the negative test adds a
    temp tree through, with no injected aliases)."""
    roots = _PKG_DIRS if roots is None else roots
    module_files = {}
    for pkg, base in roots.items():
        for f in Path(base).rglob("*.py"):
            rel = f.relative_to(base).with_suffix("")
            parts = [pkg] + [p for p in rel.parts if p != "__init__"]
            module_files[".".join(parts)] = f
    return module_files


_MODULE_FILES = build_module_files()

_SCRIPTS_AND_TESTS = sorted(
    list(REPO.glob("scripts/**/*.py")) + list(REPO.glob("tests/**/*.py"))
)


def _conftest_sys_path_roots():
    """Extra module roots configured by conftest.py / pytest.ini:
    ``pythonpath = ...`` entries and literal ``sys.path.insert`` /
    ``sys.path.append`` arguments in conftest files — the places pytest
    makes importable besides the rootdir."""
    roots = []
    ini = REPO / "pytest.ini"
    if ini.exists():
        for line in ini.read_text().splitlines():
            line = line.strip()
            if line.startswith("pythonpath"):
                for p in line.split("=", 1)[1].split():
                    roots.append(REPO / p)
    for conftest in sorted(REPO.glob("**/conftest.py")):
        try:
            tree = ast.parse(conftest.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("insert", "append")
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "path"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys"
            ):
                continue
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    p = Path(a.value)
                    roots.append(p if p.is_absolute() else conftest.parent / p)
    return roots


_EXTRA_ROOTS = _conftest_sys_path_roots()


def resolve_import(name, importer, module_files=None):
    """Resolve ``name`` to a local file ``Path``, or ``None``.

    Order: the repo index, then the importing file's own directory
    (bare sibling imports, pytest rootdir/conftest style), then the
    configured ``sys.path`` roots. Both ``pkg/mod.py`` and
    ``pkg/mod/__init__.py`` layouts count.
    """
    module_files = _MODULE_FILES if module_files is None else module_files
    f = module_files.get(name)
    if f is not None:
        return f
    parts = Path(*name.split("."))
    for base in (Path(importer).parent, *_EXTRA_ROOTS):
        cand = base / parts.with_suffix(".py")
        if cand.exists():
            return cand
        cand = base / parts / "__init__.py"
        if cand.exists():
            return cand
    return None


def classify_import(name, importer, module_files=None):
    """``"local"`` | ``"stdlib"`` | ``"thirdparty"`` | ``None`` for a
    single import name in file ``importer``; ``None`` = unresolved."""
    if resolve_import(name, importer, module_files) is not None:
        return "local"
    top = name.split(".")[0]
    try:
        spec = importlib.util.find_spec(top)
    except Exception:
        return None
    if spec is None:
        return None
    origin = spec.origin or ""
    if origin in ("built-in", "frozen", "namespace"):
        return "stdlib"
    stdlib_dir = os.path.dirname(os.__file__)
    if os.path.abspath(origin).startswith(os.path.abspath(stdlib_dir)):
        return "stdlib"
    return "thirdparty"


def _imports(path):
    """Module names this file imports (absolute names where resolvable),
    at module and function level."""
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


def _module_level_imports(path):
    """Module names imported at module scope (not inside a function/class)."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return set()
    names = set()

    def visit(stmt):
        if isinstance(stmt, ast.Import):
            names.update(a.name for a in stmt.names)
        elif isinstance(stmt, ast.ImportFrom):
            names.add(stmt.module or "")
            for a in stmt.names:
                names.add((stmt.module or "") + "." + a.name)
        elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.While, ast.For)):
            for sub in stmt.body + stmt.orelse:
                visit(sub)
            if isinstance(stmt, ast.Try):
                for h in stmt.handlers:
                    for sub in h.body:
                        visit(sub)
                for sub in stmt.finalbody:
                    visit(sub)

    for stmt in tree.body:
        visit(stmt)
    return names


def _declared_venvs(path):
    """The set of venvs a file's docstring declares: ``{"engine"}``,
    ``{"api"}`` or ``{"engine", "api"}``; empty when undeclared."""
    try:
        doc = ast.get_docstring(ast.parse(path.read_text())) or ""
    except SyntaxError:
        return set()
    return set(re.findall(r"\.venv-(engine|api)", doc))


def _running_venv():
    return "engine" if sys.version_info < (3, 10) else "api"


def _is_api_module(name):
    return name == FORBIDDEN_PREFIX or name.startswith(FORBIDDEN_PREFIX + ".")


def _closure(seeds, module_files=None):
    """Transitive local-import closure over ``seeds``.

    Returns ``(api_edges, seen)``: ``api_edges`` is a list of
    ``(file, imported_name)`` where the name is a ``holdem_solver.api``
    module; ``seen`` is the set of repo files walked.
    """
    module_files = _MODULE_FILES if module_files is None else module_files
    frontier = set(seeds)
    seen = set()
    api_edges = []
    while frontier:
        f = frontier.pop()
        if f in seen:
            continue
        seen.add(f)
        for name in _imports(f):
            if _is_api_module(name):
                api_edges.append((f, name))
                continue
            target = resolve_import(name, f, module_files)
            if target is not None and target not in seen:
                frontier.add(target)
    return api_edges, seen


def _engine_seeds():
    seeds = {f for d in ENGINE_DIRS for f in sorted(d.glob("*.py"))}
    for f in _SCRIPTS_AND_TESTS:
        imports = _imports(f)
        declared = _declared_venvs(f)
        if "engine" in declared or (
            "api" not in declared
            and any(
                n == "gpu_cfr" or n.startswith("gpu_cfr.")
                or n.startswith("holdem_solver.engine_worker")
                for n in imports
            )
        ):
            seeds.add(f)
    return seeds


def _declared_venv_violations(files, module_files=None):
    """``[(file, problem), ...]`` for files whose declared venvs contradict
    their import closure."""
    module_files = _MODULE_FILES if module_files is None else module_files
    problems = []
    for f in files:
        declared = _declared_venvs(f)
        api_edges, _ = _closure([f], module_files)
        if api_edges and "engine" in declared:
            src, name = api_edges[0]
            problems.append(
                (f, "declares .venv-engine but reaches %s (via %s)"
                 % (name, src))
            )
        if "api" in declared:
            for name in _module_level_imports(f):
                if name == "gpu_cfr" or name.startswith("gpu_cfr."):
                    problems.append(
                        (f, "declares .venv-api but imports %s at module "
                            "level (engine-only runtime)" % name)
                    )
                    break
    return problems


def test_every_script_and_test_declares_venv():
    undeclared = [f for f in _SCRIPTS_AND_TESTS if not _declared_venvs(f)]
    assert not undeclared, (
        "scripts/tests files with no venv declaration in their docstring:\n"
        + "\n".join("  %s" % f.relative_to(REPO) for f in undeclared)
    )


def test_engine_venv_never_imports_api():
    api_edges, seen = _closure(_engine_seeds())
    assert seen  # the walk actually covered files
    assert not api_edges, (
        "engine-venv code must never import holdem_solver.api:\n"
        + "\n".join("  %s imports %s" % (f, n) for f, n in api_edges)
    )


def test_every_import_classifies():
    """Every import in every file declaring the running venv resolves as
    stdlib, installed third-party, or local — nothing unresolved."""
    unresolved = []
    counts = {"local": 0, "stdlib": 0, "thirdparty": 0}
    for f in _SCRIPTS_AND_TESTS:
        if _running_venv() not in _declared_venvs(f):
            continue
        for name in sorted(_imports(f)):
            if not name:
                continue
            kind = classify_import(name, f)
            if kind is None:
                unresolved.append((f, name))
            else:
                counts[kind] += 1
    assert counts["local"] and counts["stdlib"], counts
    assert not unresolved, (
        "unresolved imports:\n"
        + "\n".join("  %s imports %s" % (f.relative_to(REPO), n)
                    for f, n in unresolved)
    )


def test_bare_sibling_imports_resolve():
    """``from test_evwalk import ...`` inside tests/ resolves to the
    sibling file — no aliases injected."""
    self_file = REPO / "tests" / "test_compare_cells.py"
    assert (
        resolve_import("test_evwalk", self_file)
        == REPO / "tests" / "test_evwalk.py"
    )


def test_declared_venv_matches_imports():
    problems = _declared_venv_violations(_SCRIPTS_AND_TESTS)
    assert not problems, (
        "declared venv contradicts imports:\n"
        + "\n".join("  %s: %s" % (f.relative_to(REPO), p) for f, p in problems)
    )


def test_declared_venv_matches_imports_negative(tmp_path):
    """A .venv-engine script reaching holdem_solver.api through a bare
    sibling import chain must be flagged — the checker must not pass the
    case it exists for. Built through the same indexer production uses:
    the tmp tree is an extra resolution root, no injected aliases."""
    (tmp_path / "helper.py").write_text(
        "from holdem_solver.api import evec\n"
    )
    (tmp_path / "mid.py").write_text("import helper\n")
    script = tmp_path / "script.py"
    script.write_text('"""Runs in .venv-engine."""\nimport mid\n')
    # Bare sibling resolution finds mid/helper through the importing
    # file's own directory — the production indexer runs unmodified.
    problems = _declared_venv_violations([script])
    assert problems and problems[0][0] == script
    assert "holdem_solver.api" in problems[0][1]


def test_unresolved_import_fails(tmp_path):
    """An import that is neither stdlib, installed, nor local is
    unresolved — classify_import returns None for it."""
    f = tmp_path / "x.py"
    f.write_text("import no_such_module_xyz_123\n")
    assert classify_import("no_such_module_xyz_123", f) is None
    # and the real imports classify correctly
    assert classify_import("os", f) == "stdlib"
    assert classify_import("pytest", f) == "thirdparty"
    assert classify_import("holdem_solver.api.nodes", f) == "local"


def test_engine_runs_install_api_import_hook():
    """In engine-venv test runs a meta_path hook raises on
    ``holdem_solver.api`` imports (see tests/conftest.py)."""
    if sys.version_info >= (3, 10):
        import pytest
        pytest.skip("the hook is installed only in the engine venv")
    import importlib
    try:
        importlib.import_module("holdem_solver.api")
    except ImportError as exc:
        assert "forbidden in the engine venv" in str(exc)
    else:
        raise AssertionError(
            "holdem_solver.api imported in the engine venv — the boundary "
            "hook in tests/conftest.py is missing"
        )
