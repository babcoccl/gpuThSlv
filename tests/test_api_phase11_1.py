"""Phase 11.1: install.sh --check preflight, interpreter discovery, fake
engine install path (docs/PHASE11_1.md TC1-TC4).

Shell-level tests: fake interpreters are tiny scripts that print a version.
No root, no systemd changes. Runs in ``.venv-api``."""

import getpass
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "scripts" / "release"
INSTALL = RELEASE / "install.sh"
NOUSER = "holdem-test-nouser"


def fake_python(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {version}\n")
    path.chmod(0o755)
    return path


def uv_layout(root: Path, api="3.15.2", engine="3.9.25"):
    a = fake_python(root / f"cpython-{api}-linux-x86_64-gnu/bin/python{api.rsplit('.', 1)[0]}", api)
    e = fake_python(root / f"cpython-{engine}-linux-x86_64-gnu/bin/python{engine.rsplit('.', 1)[0]}", engine)
    return a, e


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def box(tmp_path):
    """A sandbox: fake python root, empty system dirs, stub npm/node."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("npm", "node"):
        (bindir / tool).write_text("#!/bin/sh\nexit 0\n")
        (bindir / tool).chmod(0o755)
    (tmp_path / "sys").mkdir()
    (tmp_path / "units").mkdir()
    (tmp_path / "userunits").mkdir()
    uv_layout(tmp_path / "py")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOLDEM_PREFIX": str(tmp_path / "opt/holdem"),
        "HOLDEM_DATA_DIR": str(tmp_path / "var/lib/holdem"),
        "HOLDEM_CONF_DIR": str(tmp_path / "etc/holdem"),
        "HOLDEM_RUN_DIR": str(tmp_path / "run/holdem"),
        "HOLDEM_USER": getpass.getuser(),
        "HOLDEM_PYTHON_ROOT": str(tmp_path / "py"),
        "HOLDEM_SYSTEM_PYTHON_DIRS": str(tmp_path / "sys"),
        "HOLDEM_FORBIDDEN_PREFIXES": str(tmp_path / "home"),
        "HOLDEM_INSTALL_ENGINE": "fake",
        "HOLDEM_API_PORT": str(free_port()),
        "HOLDEM_SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "HOLDEM_USER_UNIT_DIRS": str(tmp_path / "userunits"),
        "HOLDEM_CHECK_PROCS": "0",
        "HOLDEM_NO_SYSTEMD": "1",
    }
    for var in ("HOLDEM_API_PYTHON", "HOLDEM_ENGINE_PYTHON", "HOLDEM_STUB_VENVS",
                "HOLDEM_ALLOW_COEXIST", "HOLDEM_GPUCFR_DIR"):
        env.pop(var, None)
    return tmp_path, env


def check(env, *, systemd=False, **extra):
    env = {**env, **{k: str(v) for k, v in extra.items()}}
    if systemd:
        env.pop("HOLDEM_NO_SYSTEMD", None)
    p = subprocess.run(["bash", str(INSTALL), "--check"], env=env,
                       capture_output=True, text=True)
    items = {}
    for line in p.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] in ("PASS", "FAIL", "SKIP"):
            items.setdefault(parts[1], []).append((parts[0], parts[2] if len(parts) > 2 else ""))
    return p.returncode, items, p.stdout + p.stderr


def status(items, name):
    return {s for s, _ in items[name]}


def detail(items, name):
    return " ".join(d for _, d in items[name])


def tree(path: Path):
    return sorted(str(p) for p in path.rglob("*"))


# -- TC3: --check all pass, changes nothing --------------------------------------

def test_check_all_pass_and_changes_nothing(box):
    tmp, env = box
    before = tree(tmp)
    rc, items, out = check(env)
    assert rc == 0, out
    assert "preflight: PASS" in out
    for name in ("interp.api", "version.api", "traverse.api",
                 "interp.engine", "version.engine", "traverse.engine", "tools", "port", "disk"):
        assert status(items, name) == {"PASS"}, (name, out)
    assert status(items, "gpucfr") == {"SKIP"} and status(items, "gpus") == {"SKIP"}
    assert tree(tmp) == before  # nothing created (no prefix, data dir, env file)


# -- TC1: interpreter discovery ---------------------------------------------------

def test_discovery_explicit_then_root_then_system(box):
    tmp, env = box
    sys_api = fake_python(tmp / "sys/python3.15", "3.15.2")
    explicit = fake_python(tmp / "explicit/python3.15", "3.15.2")
    _, items, out = check(env, HOLDEM_API_PYTHON=explicit)
    assert detail(items, "interp.api").startswith(str(explicit)), out
    _, items, out = check(env)
    assert detail(items, "interp.api").startswith(str(tmp / "py")), out
    shutil.rmtree(tmp / "py")
    _, items, out = check(env)
    assert detail(items, "interp.api").startswith(str(sys_api)), out


def test_discovery_newest_patch_no_freethreaded(box):
    tmp, env = box
    fake_python(tmp / "py/cpython-3.9.30+freethreaded-linux-x86_64-gnu/bin/python3.9", "3.9.30")
    fake_python(tmp / "py/cpython-3.9.7-linux-x86_64-gnu/bin/python3.9", "3.9.7")
    _, items, out = check(env)
    assert "cpython-3.9.25-" in detail(items, "interp.engine"), out


def test_never_searches_home(box):
    tmp, env = box
    shutil.rmtree(tmp / "py")
    home_bin = tmp / "home/u/.local/bin"
    fake_python(home_bin / "python3.9", "3.9.25")
    rc, items, out = check(env, HOME=tmp / "home/u",
                           HOLDEM_SYSTEM_PYTHON_DIRS=f"{home_bin} {tmp / 'sys'}")
    assert rc != 0 and status(items, "interp.engine") == {"FAIL"}, out
    assert str(home_bin) not in detail(items, "interp.engine").split(" in ")[0]
    # no $HOME default anywhere in the release scripts' interpreter logic
    src = (RELEASE / "common.sh").read_text() + (RELEASE / "preflight.sh").read_text()
    assert ".local/bin/python" not in src and "/usr/bin/python3.15" not in src


@pytest.mark.parametrize("kind,ver", [("engine", "3.10.12"), ("engine", "3.8.18"),
                                      ("api", "3.15.0"), ("api", "3.14.2")])
def test_wrong_version_fails_with_fix(box, kind, ver):
    tmp, env = box
    var = f"HOLDEM_{kind.upper()}_PYTHON"
    py = fake_python(tmp / "other" / "python", ver)
    rc, items, out = check(env, **{var: py})
    assert rc != 0 and status(items, f"version.{kind}") == {"FAIL"}, out
    d = detail(items, f"version.{kind}")
    assert ver in d and var in d and "uv python install" in d and "UV_PYTHON_INSTALL_DIR=" in d


def test_missing_interpreter_fails_with_fix(box):
    tmp, env = box
    shutil.rmtree(tmp / "py")
    rc, items, out = check(env)
    assert rc != 0
    for kind, inst in (("api", "python install --no-bin 3.15.2"), ("engine", "python install --no-bin 3.9")):
        d = detail(items, f"interp.{kind}")
        assert status(items, f"interp.{kind}") == {"FAIL"}
        assert f"HOLDEM_{kind.upper()}_PYTHON" in d and inst in d, d
    # explicit path that does not exist
    rc, items, _ = check(env, HOLDEM_ENGINE_PYTHON=tmp / "nope/python3.9")
    assert rc != 0 and "not executable" in detail(items, "interp.engine")


def test_home_like_prefix_fails_under_systemd(box):
    tmp, env = box
    home_py = fake_python(tmp / "home/u/.local/share/uv/python/cpython-3.9.25/bin/python3.9", "3.9.25")
    _, items, out = check(env, systemd=True, HOLDEM_ENGINE_PYTHON=home_py)
    assert status(items, "prefix.engine") == {"FAIL"}, out
    assert "HOLDEM_ENGINE_PYTHON" in detail(items, "prefix.engine")
    # a system-path symlink whose real path is in a home dir also fails
    link = tmp / "sys/python3.9"
    link.symlink_to(home_py)
    _, items, out = check(env, systemd=True, HOLDEM_ENGINE_PYTHON=link)
    assert status(items, "prefix.engine") == {"FAIL"}, out
    # NO_SYSTEMD smoke: ProtectHome not in effect -> SKIP, still version-checked
    rc, items, out = check(env, HOLDEM_ENGINE_PYTHON=home_py)
    assert status(items, "prefix.engine") == {"SKIP"} and rc == 0, out


def test_untraversable_for_service_user(box):
    tmp, env = box
    base = Path(tempfile.mkdtemp(prefix="holdem-trav-", dir="/tmp"))
    try:
        base.chmod(0o755)
        api, eng = uv_layout(base / "py")
        for p in [base / "py", *[d for d in eng.parents if str(d).startswith(str(base / "py"))]]:
            p.chmod(0o755)
        for d in api.parents:
            if str(d).startswith(str(base / "py")):
                d.chmod(0o755)
        rc, items, out = check(env, systemd=True, HOLDEM_USER=NOUSER, HOLDEM_PYTHON_ROOT=base / "py")
        assert status(items, "traverse.engine") == {"PASS"}, out
        eng.parent.chmod(0o700)  # bin/ not world-traversable
        rc, items, out = check(env, systemd=True, HOLDEM_USER=NOUSER, HOLDEM_PYTHON_ROOT=base / "py")
        assert status(items, "traverse.engine") == {"FAIL"}, out
        d = detail(items, "traverse.engine")
        assert str(eng.parent) in d and NOUSER in d and "HOLDEM_ENGINE_PYTHON" in d
        eng.parent.chmod(0o755)
        eng.chmod(0o750)  # interpreter itself not world-executable
        rc, items, out = check(env, systemd=True, HOLDEM_USER=NOUSER, HOLDEM_PYTHON_ROOT=base / "py")
        assert status(items, "traverse.engine") == {"FAIL"}, out
    finally:
        for p in base.rglob("*"):
            if p.is_dir():
                p.chmod(0o755)
        shutil.rmtree(base)


def test_sudo_simulation_home_root(box):
    """Under sudo HOME=/root: discovery must not look there, and the default
    forbidden-prefix list rejects an explicit /root interpreter."""
    tmp, env = box
    env = {**env, "HOME": "/root", "USER": "root", "SUDO_USER": getpass.getuser()}
    env.pop("HOLDEM_FORBIDDEN_PREFIXES")
    rc, items, out = check(env, HOLDEM_SYSTEM_PYTHON_DIRS=f"/root/.local/bin {tmp / 'sys'}")
    assert rc == 0, out
    assert detail(items, "interp.engine").startswith(str(tmp / "py"))
    _, items, out = check(env, systemd=True, HOLDEM_ENGINE_PYTHON="/root/.local/bin/python3.9")
    assert status(items, "interp.engine") == {"FAIL"} or status(items, "prefix.engine") == {"FAIL"}, out


# -- TC3: other preflight items ---------------------------------------------------

def gpucfr_tree(root: Path) -> Path:
    pkg = root / "gpu_cfr"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.cpython-39-x86_64-linux-gnu.so").write_bytes(b"\x7fELF")
    return root


def test_gpucfr_checks_real_engine_only(box):
    tmp, env = box
    smi = fake_python(tmp / "bin/nvidia-smi-ok", "")
    smi.write_text('#!/bin/sh\necho "GPU $2: stub"\n')
    real = {"HOLDEM_INSTALL_ENGINE": "real", "HOLDEM_NVIDIA_SMI": smi}
    rc, items, out = check(env, HOLDEM_GPUCFR_DIR=tmp / "missing", **real)
    assert rc != 0 and status(items, "gpucfr") == {"FAIL"}
    assert "submodule update --init third_party/GPU-CFR" in detail(items, "gpucfr")
    (tmp / "empty").mkdir()
    _, items, _ = check(env, HOLDEM_GPUCFR_DIR=tmp / "empty", **real)
    assert "not initialized" in detail(items, "gpucfr")
    good = gpucfr_tree(tmp / "gc")
    rc, items, out = check(env, HOLDEM_GPUCFR_DIR=good, **real)
    assert status(items, "gpucfr") == {"PASS"} and rc == 0, out
    # unreadable by a service user that is not the owner
    (good / "gpu_cfr/config.cpython-39-x86_64-linux-gnu.so").chmod(0o600)
    rc, items, out = check(env, systemd=True, HOLDEM_GPUCFR_DIR=good, HOLDEM_USER=NOUSER,
                           HOLDEM_PYTHON_ROOT=tmp / "py", **real)
    assert status(items, "gpucfr") == {"FAIL"} and "chmod -R o+rX" in detail(items, "gpucfr"), out
    # fake engine never checks GPU-CFR
    _, items, _ = check(env, HOLDEM_GPUCFR_DIR=tmp / "missing")
    assert status(items, "gpucfr") == {"SKIP"}


def test_gpus_real_engine(box):
    tmp, env = box
    good = gpucfr_tree(tmp / "gc")
    one = tmp / "bin/smi-one"
    one.write_text('#!/bin/sh\n[ "$2" = 0 ] && echo "GPU 0: stub"\nexit 0\n')
    one.chmod(0o755)
    base = {"HOLDEM_INSTALL_ENGINE": "real", "HOLDEM_GPUCFR_DIR": good, "HOLDEM_GPUS": "0,1"}
    rc, items, out = check(env, HOLDEM_NVIDIA_SMI=one, **base)
    assert rc != 0 and status(items, "gpus") == {"FAIL"} and "1" in detail(items, "gpus"), out
    rc, items, out = check(env, HOLDEM_NVIDIA_SMI=tmp / "bin/no-smi", **base)
    assert status(items, "gpus") == {"FAIL"} and "nvidia-smi not found" in detail(items, "gpus")
    both = tmp / "bin/smi-both"
    both.write_text('#!/bin/sh\necho "GPU $2: stub"\n')
    both.chmod(0o755)
    rc, items, out = check(env, HOLDEM_NVIDIA_SMI=both, **base)
    assert rc == 0 and status(items, "gpus") == {"PASS"}, out


def test_port_in_use(box):
    tmp, env = box
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        rc, items, out = check(env, HOLDEM_API_PORT=port)
    assert rc != 0 and status(items, "port") == {"FAIL"} and str(port) in detail(items, "port"), out


def test_conflicting_units(box):
    tmp, env = box
    prefix = env["HOLDEM_PREFIX"]
    ours = tmp / "units/holdem-api.service"
    ours.write_text(f"# holdem-release-unit prefix={prefix} (rendered by scripts/release/install.sh)\n"
                    f"ExecStart={prefix}/current/.venv-api/bin/python -m holdem_solver.api\n")
    _, items, out = check(env, systemd=True)
    assert status(items, "units") == {"PASS"}, out
    foreign = tmp / "units/holdem-worker@.service"
    foreign.write_text("ExecStart=/home/u/gpuThSlv/.venv-engine/bin/python -m holdem_solver.engine_worker\n")
    # a unit rendered for a different prefix is not ours either
    other = tmp / "units/holdem-solver.target"
    other.write_text("# holdem-release-unit prefix=/srv/other (rendered by scripts/release/install.sh)\n")
    rc, items, out = check(env, systemd=True)
    assert rc != 0 and status(items, "units") == {"FAIL"} and str(foreign) in detail(items, "units"), out
    assert str(other) in detail(items, "units")
    foreign.unlink()
    other.unlink()
    # an enabled user unit (the pre-release deployment) conflicts unless coexist
    uu = tmp / "userunits"
    (uu / "holdem-api.service").write_text("ExecStart=/x\n")
    (uu / "holdem-solver.target.wants").mkdir()
    (uu / "holdem-solver.target.wants/holdem-api.service").symlink_to(uu / "holdem-api.service")
    _, items, out = check(env, systemd=True)
    assert status(items, "units") == {"FAIL"} and "holdem-api.service(enabled)" in detail(items, "units"), out
    _, items, out = check(env, systemd=True, HOLDEM_ALLOW_COEXIST=1)
    assert status(items, "units") == {"PASS"} and "coexisting" in detail(items, "units"), out


def test_disk_space(box):
    _, env = box
    rc, items, out = check(env, HOLDEM_MIN_INSTALL_FREE_BYTES=1 << 60)
    assert rc != 0 and status(items, "disk") == {"FAIL"}, out


def test_groups(box):
    _, env = box
    _, items, out = check(env, systemd=True, HOLDEM_DEVICE_GROUPS="root holdem-test-nogroup")
    assert status(items, "groups") == {"FAIL"}, out
    assert "holdem-test-nogroup" in detail(items, "groups") and "groupadd --system" in detail(items, "groups")
    _, items, out = check(env, systemd=True, HOLDEM_DEVICE_GROUPS="root")
    assert status(items, "groups") == {"PASS"}, out
    _, items, _ = check(env, HOLDEM_DEVICE_GROUPS="holdem-test-nogroup")
    assert status(items, "groups") == {"SKIP"}  # NO_SYSTEMD: no SupplementaryGroups


def test_tools_missing(box):
    _, env = box
    rc, items, out = check(env, HOLDEM_REQUIRED_TOOLS="rsync holdem-no-such-tool")
    assert rc != 0 and status(items, "tools") == {"FAIL"} and "holdem-no-such-tool" in detail(items, "tools")


def test_systemd_item(box):
    _, env = box
    _, items, out = check(env, systemd=True)
    pid1 = subprocess.run(["ps", "-p", "1", "-o", "comm="], capture_output=True, text=True).stdout.strip()
    assert status(items, "systemd") == ({"PASS"} if pid1 == "systemd" else {"FAIL"}), out


# -- install.sh aborts on preflight FAIL ------------------------------------------

def test_install_aborts_on_preflight_fail(box):
    tmp, env = box
    shutil.rmtree(tmp / "py")
    p = subprocess.run(["bash", str(INSTALL), "t1"], env=env, capture_output=True, text=True)
    assert p.returncode != 0 and "preflight failed" in p.stderr, p.stdout + p.stderr
    assert not (tmp / "opt").exists() and not (tmp / "etc").exists()


def test_install_rejects_bad_engine(box):
    _, env = box
    p = subprocess.run(["bash", str(INSTALL), "--check"], env={**env, "HOLDEM_INSTALL_ENGINE": "gpu"},
                       capture_output=True, text=True)
    assert p.returncode != 0 and "real or fake" in p.stderr


# -- TC2: fake engine on the install path -------------------------------------------

def test_install_engine_written_to_env(box):
    tmp, env = box
    env = {**env, "HOLDEM_STUB_VENVS": "1", "HOLDEM_API_PYTHON": sys.executable}
    env_file = tmp / "etc/holdem/holdem.env"
    subprocess.run(["bash", str(INSTALL), "t1"], env=env, check=True, capture_output=True)
    assert "HOLDEM_SOLVER_ENGINE=fake" in env_file.read_text().splitlines()
    key = [l for l in env_file.read_text().splitlines() if l.startswith("HOLDEM_API_KEY=")][0]
    # an explicit engine switch updates the env, keeps the secrets
    p = subprocess.run(["bash", str(INSTALL), "t2"], capture_output=True, text=True,
                       env={**env, "HOLDEM_INSTALL_ENGINE": "real",
                            "HOLDEM_GPUCFR_DIR": str(gpucfr_tree(tmp / "gc")),
                            "HOLDEM_NVIDIA_SMI": "/bin/false"})
    assert p.returncode != 0 and "gpus" in p.stdout  # real needs GPUs: preflight aborts
    smi = tmp / "bin/smi"
    smi.write_text('#!/bin/sh\necho "GPU $2: stub"\n')
    smi.chmod(0o755)
    subprocess.run(["bash", str(INSTALL), "t2"], check=True, capture_output=True,
                   env={**env, "HOLDEM_INSTALL_ENGINE": "real",
                        "HOLDEM_GPUCFR_DIR": str(tmp / "gc"), "HOLDEM_NVIDIA_SMI": str(smi)})
    lines = env_file.read_text().splitlines()
    assert "HOLDEM_SOLVER_ENGINE=real" in lines and key in lines
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_units_and_service_use_env_engine():
    unit = (ROOT / "deploy/systemd/system/holdem-worker@.service").read_text()
    assert "--engine ${HOLDEM_SOLVER_ENGINE} --gpu %i" in unit
    assert "--engine real" not in unit
    svc = (RELEASE / "service.sh").read_text()
    assert '--engine "${HOLDEM_SOLVER_ENGINE:-real}"' in svc and "--engine real" not in svc


def test_fake_requirements_have_no_cuda():
    reqs = (ROOT / "requirements/engine-fake.txt").read_text().lower()
    pins = [l.split("==")[0] for l in reqs.splitlines() if l and not l.startswith("#")]
    assert pins == ["numpy"]
    real = (ROOT / "requirements/engine.txt").read_text()
    numpy_real = [l for l in real.splitlines() if l.startswith("numpy==")]
    assert numpy_real and numpy_real[0] in reqs  # same pin as the real engine venv


def test_fake_engine_never_imports_gpu_cfr():
    code = ("import sys; from holdem_solver.engine_worker.server import _make_engine; "
            "_make_engine('fake'); "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('gpu_cfr', 'torch', 'triton')]; "
            "assert not bad, bad; print('ok')")
    pythons = [sys.executable]
    eng = ROOT / ".venv-engine/bin/python"
    if eng.exists():
        pythons.append(str(eng))
    for py in pythons:
        out = subprocess.run([py, "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": str(ROOT)})
        assert out.returncode == 0 and "ok" in out.stdout, (py, out.stderr)


# -- TC4/TC5: smoke + acceptance scripts --------------------------------------------

def test_smoke_runs_preflight_and_logs_tier_engine():
    smoke = (RELEASE / "smoke.sh").read_text()
    assert '"$HERE/install.sh" --check' in smoke
    assert "$TIER-$ENGINE.log" in smoke and "acceptance_header" in smoke
    acc = (RELEASE / "acceptance-root.sh").read_text()
    assert '"$HERE/install.sh" --check' in acc
    assert "$TIER-$ENGINE.log" in acc and "acceptance_header" in acc
    steps = (RELEASE / "smoke-steps.sh").read_text()
    assert "# tier:" in steps and "# engine:" in steps


@pytest.mark.skipif(os.geteuid() == 0, reason="needs a non-root user")
def test_acceptance_root_refuses_without_root():
    p = subprocess.run(["bash", str(RELEASE / "acceptance-root.sh"), "--engine", "fake"],
                       capture_output=True, text=True)
    assert p.returncode != 0 and "T4" in p.stderr


def test_detect_tier_t4_without_root():
    p = subprocess.run(["bash", "-c", f"source {RELEASE}/common.sh; detect_tier"],
                       capture_output=True, text=True, check=True)
    assert p.stdout.strip() == ("T4" if os.geteuid() != 0 else p.stdout.strip())


def test_phase11_units_without_marker_are_ours_on_upgrade(box):
    tmp, env = box
    prefix = Path(env["HOLDEM_PREFIX"])
    (prefix / "releases/v1").mkdir(parents=True)
    (prefix / "current").symlink_to(prefix / "releases/v1")
    (tmp / "units/holdem-api.service").write_text(
        f"ExecStart={prefix}/current/.venv-api/bin/python -m holdem_solver.api\n")
    (tmp / "units/holdem-solver.target").write_text(
        "[Unit]\nDescription=holdem-solver (API + one engine worker per GPU)\n")
    _, items, out = check(env, systemd=True)
    assert status(items, "units") == {"PASS"}, out
    (prefix / "current").unlink()  # no release installed: same files are foreign
    _, items, out = check(env, systemd=True)
    assert status(items, "units") == {"FAIL"}, out


def test_port_held_by_degraded_installed_api_passes(box):
    import http.server
    import threading

    tmp, env = box

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503 if self.path == "/healthz" else 404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        _, items, out = check(env, HOLDEM_API_PORT=port)
        assert status(items, "port") == {"FAIL"}, out  # nothing installed: foreign
        prefix = Path(env["HOLDEM_PREFIX"])
        (prefix / "releases/v1").mkdir(parents=True)
        (prefix / "current").symlink_to(prefix / "releases/v1")
        _, items, out = check(env, HOLDEM_API_PORT=port)
        assert status(items, "port") == {"PASS"} and "503" in detail(items, "port"), out
    finally:
        srv.shutdown()
