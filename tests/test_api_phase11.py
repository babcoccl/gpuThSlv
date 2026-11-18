"""Phase 11: /healthz checks, /version, license doc, release scripts.

Runs in ``.venv-api``."""

import os
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holdem_solver.api import create_app

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "scripts" / "release"


def make_client(tmp_path, **kwargs):
    return TestClient(create_app(data_dir=str(tmp_path / "api"), **kwargs))


def test_healthz_ok_reports_workers_gpus_disk(tmp_path):
    with make_client(tmp_path, gpus=[], engine="fake", api_key="k") as client:
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["disk"]["free_bytes"] > 0 and body["disk"]["ok"] is True


def test_healthz_degraded_when_disk_full(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLDEM_HEALTH_MIN_FREE_BYTES", str(10**18))
    app = create_app(gpus=[], engine="fake", api_key="k", data_dir=str(tmp_path / "c"))
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
        assert resp.json()["disk"]["ok"] is False


def test_healthz_degraded_when_worker_down(tmp_path):
    from holdem_solver.api.scheduler import SchedulerConfig
    cfg = SchedulerConfig(worker_mode="external")
    app = create_app(gpus=[0], engine="fake", api_key="k",
                     data_dir=str(tmp_path / "d"), scheduler_config=cfg,
                     client_kwargs={0: {"start_timeout": 0.1}})
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 503
        assert resp.json()["workers"]["0"]["up"] is False


def test_version_endpoint(tmp_path):
    import holdem_solver
    with make_client(tmp_path, gpus=[], api_key="k") as client:
        body = client.get("/version").json()
        assert body["version"] == holdem_solver.__version__


def test_no_vendored_binaries():
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True,
    ).splitlines()
    bad = [f for f in out if f.endswith((".so", ".zip", ".whl", ".bin", ".exe"))]
    assert not bad, f"vendored binaries tracked: {bad}"
    assert "third_party/GPU-CFR" in out  # submodule gitlink only


def test_licenses_doc_complete():
    text = (ROOT / "docs" / "LICENSES.md").read_text()
    for needle in (
        "no license file", "private-use", "not redistributed",
        "GPU-CFR", "TexasSolver", "third_party/GPU-CFR",
        "pip-licenses", "license-checker", "CUDA",
    ):
        assert needle in text, f"LICENSES.md missing {needle!r}"


def test_release_scripts_parse():
    for script in sorted(RELEASE.glob("*.sh")):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_install_idempotent_stub(tmp_path):
    """install.sh with stubbed venvs creates the layout and keeps secrets."""
    env = {
        **os.environ,
        "HOLDEM_PREFIX": str(tmp_path / "opt/holdem"),
        "HOLDEM_DATA_DIR": str(tmp_path / "var/lib/holdem"),
        "HOLDEM_CONF_DIR": str(tmp_path / "etc/holdem"),
        "HOLDEM_RUN_DIR": str(tmp_path / "run/holdem"),
        "HOLDEM_NO_SYSTEMD": "1",
        "HOLDEM_STUB_VENVS": "1",
        "HOLDEM_API_PYTHON": "/usr/bin/python3",
    }
    for _ in range(2):
        subprocess.run(
            ["bash", str(RELEASE / "install.sh"), "t1"], env=env, check=True,
            capture_output=True, text=True,
        )
    prefix = tmp_path / "opt/holdem"
    env_file = tmp_path / "etc/holdem/holdem.env"
    assert (prefix / "current").is_symlink()
    assert (prefix / "releases/t1/holdem_solver/api/app.py").exists()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    text = env_file.read_text()
    key1 = [l for l in text.splitlines() if l.startswith("HOLDEM_API_KEY=")][0]
    assert "HOLDEM_SESSION_SECRET=" in text
    # second run kept the same key
    assert key1 in env_file.read_text()


def test_backup_restore_roundtrip(tmp_path):
    """backup.sh copies the DB; restore.sh puts it back (no systemd)."""
    data = tmp_path / "var/lib/holdem"
    data.mkdir(parents=True)
    env = {
        **os.environ,
        "HOLDEM_PREFIX": str(tmp_path / "opt/holdem"),
        "HOLDEM_DATA_DIR": str(data),
        "HOLDEM_CONF_DIR": str(tmp_path / "etc/holdem"),
        "HOLDEM_RUN_DIR": str(tmp_path / "run/holdem"),
        "HOLDEM_BACKUP_DIR": str(tmp_path / "var/lib/holdem/backups"),
        "HOLDEM_NO_SYSTEMD": "1",
    }
    # real sqlite DB needed for the online backup
    import sqlite3
    conn = sqlite3.connect(data / "solves.db")
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    out = subprocess.run(
        ["bash", str(RELEASE / "backup.sh")], env=env, check=True,
        capture_output=True, text=True,
    )
    dest = Path(out.stdout.strip().splitlines()[-1])
    assert (dest / "solves.db").exists() and (dest / "manifest.json").exists()
    (data / "solves.db").unlink()
    subprocess.run(
        ["bash", str(RELEASE / "restore.sh"), str(dest)], env=env, check=True,
        capture_output=True, text=True,
    )
    assert (data / "solves.db").exists()


def test_login_rate_limited(tmp_path):
    with make_client(tmp_path, gpus=[], engine="fake", api_key="k",
                     login_rate_per_minute=3.0, login_burst=1) as client:
        codes = [client.post("/v1/auth/login", json={"key": "bad"}).status_code
                 for _ in range(4)]
        assert 429 in codes


def test_login_sets_secure_httponly_samesite(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLDEM_SECURE_COOKIES", "1")
    app = create_app(gpus=[], engine="fake", api_key="k", data_dir=str(tmp_path / "s"))
    with TestClient(app) as client:
        resp = client.post("/v1/auth/login", json={"key": "k"})
        cookie = resp.headers.get("set-cookie", "")
        assert "Secure" in cookie and "HttpOnly" in cookie
        assert "samesite=strict" in cookie.lower()
