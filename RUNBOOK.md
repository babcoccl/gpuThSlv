# RUNBOOK

Single-host release operations. Layout:

- releases: `/opt/holdem/releases/<version>/`, live one at `/opt/holdem/current`
- secrets/env: `/etc/holdem/holdem.env` (root-only `0600`)
- data (job DB, saved spots, strategies, backups): `/var/lib/holdem`
- sockets: `/run/holdem` (RuntimeDirectory)
- services: `holdem-api.service`, `holdem-worker@0.service`,
  `holdem-worker@1.service`, all under `holdem-solver.target`

## Prerequisites

The units run with `ProtectHome=yes`, so both interpreters must live on a
system path — never under `$HOME`, `/root` or `/run/user`. Engine Python is
exactly 3.9.x (GPU-CFR ships cpython-39 `.so` files); API Python is exactly
3.15.2.

```bash
# 1. interpreters under /opt/holdem/python (uv layout; --no-bin: no links in /root)
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
sudo env UV_PYTHON_INSTALL_DIR=/opt/holdem/python "$(command -v uv)" python install --no-bin 3.9 3.15.2

# 2. GPU-CFR submodule (real engine), readable by the service user
git submodule update --init third_party/GPU-CFR
chmod -R o+rX third_party/GPU-CFR/gpu_cfr

# 3. optional: pin the interpreters (otherwise discovered in /opt/holdem/python,
#    then /usr/local/bin, /usr/bin)
export HOLDEM_API_PYTHON=/opt/holdem/python/cpython-3.15.2-linux-x86_64-gnu/bin/python3.15
export HOLDEM_ENGINE_PYTHON="$(ls -d /opt/holdem/python/cpython-3.9.*-linux-x86_64-gnu | sort -V | tail -1)/bin/python3.9"

# 4. preflight: no root needed, changes nothing, PASS/FAIL per item
scripts/release/install.sh --check
# as root it also checks traversal as the real `holdem` user (runuser)
sudo env PATH="$PATH" HOLDEM_API_PYTHON="$HOLDEM_API_PYTHON" \
    HOLDEM_ENGINE_PYTHON="$HOLDEM_ENGINE_PYTHON" scripts/release/install.sh --check
```

`--check` items: interpreters (found, exact version, not under a home dir,
`holdem` can traverse every path component), `rsync`/`npm`/`node`/`curl`,
GPU-CFR present/initialized/readable and `nvidia-smi` sees every
`HOLDEM_GPUS` GPU (real engine only), port free, conflicting existing units
or processes (listed), disk space, systemd as PID 1, `video`/`render`
groups (created by `install.sh` as root). `install.sh` runs the same checks
first and aborts on any FAIL. Under `sudo`, root's `PATH` lacks nvm's
`node`: pass `sudo env PATH="$PATH"`.

`HOLDEM_INSTALL_ENGINE=real|fake` (default `real`) sets
`HOLDEM_SOLVER_ENGINE` in the env file; `fake` needs no GPU and installs
`requirements/engine-fake.txt` (acceptance on GPU-less machines only).

## First install

```bash
scripts/release/install.sh --check                      # all PASS
sudo env PATH="$PATH" scripts/release/install.sh v1.0.0 # build, units, start
sudo systemctl status 'holdem-*'
curl -fsS http://127.0.0.1:8000/healthz                 # "status":"ok", gpus 0+1 true
sudo grep '^HOLDEM_API_KEY=' /etc/holdem/holdem.env     # key for /v1/auth/login
```

Full acceptance on a fresh host (installs, exercises, then **purges** the
install; refuses to run over an existing one):

```bash
sudo env PATH="$PATH" scripts/release/acceptance-root.sh --engine real
# log: scripts/release/smoke-results/<ts>-<tier>-real.log
```

If the old user-unit deployment is still on `:8000`, run it beside it with
`HOLDEM_API_PORT=8100 HOLDEM_ALLOW_COEXIST=1` (both share the GPUs; run in a
quiet window), or do the cutover first.

## Cutover from the user-unit deployment

The pre-release deployment runs `deploy/install-user-units.sh` user units
(`holdem-api.service`, `holdem-worker@0/1.service`, `holdem-solver.target`
in `~<user>/.config/systemd/user`), data in
`~<user>/.local/share/holdem-solver`, env in
`~<user>/.config/holdem-solver/holdem.env`, API on `:8000`.

```bash
sudo scripts/release/cutover.sh --dry-run                # identify units, data dir, port, check job
sudo env PATH="$PATH" scripts/release/cutover.sh v1.0.0  # [--old-user U] [--job ID]
```

`cutover.sh`: backs up the old DB (online SQLite), unit files and env to
`/var/backups/holdem-cutover/<ts>/`; stops and disables the old units;
installs the release without starting it; copies strategy files to
`/var/lib/holdem`, rewrites `strategy_path`, migrates the DB with
`restore.sh`; keeps the old API key; starts `holdem-solver.target`; verifies
`/healthz` and `/node` of the newest pre-cutover job. Any failure after the
old units stopped rolls back automatically. The old data dir is only read.

Rollback (exact):

```bash
sudo scripts/release/cutover.sh --rollback
# = systemctl stop/disable holdem-solver.target holdem-api holdem-worker@{0,1}
#   then, as the old user: systemctl --user enable + start holdem-solver.target
#   and verify old /healthz + the same job's /node
```

Jobs created on the release after the cutover are not in the old DB; they
stay in `/var/lib/holdem`. Rehearsed on a VM with
`scripts/release/cutover-rehearsal.sh` (never on the outpost).

## Start / stop / status

```bash
sudo systemctl start holdem-solver.target     # API + both workers
sudo systemctl stop  holdem-solver.target
sudo systemctl status 'holdem-*'
curl -fsS http://127.0.0.1:8000/healthz       # 200 ok / 503 degraded
curl -fsS http://127.0.0.1:8000/version
```

## Logs

Everything logs to journald (retention capped by
`/etc/systemd/journald.conf.d/holdem.conf`: `SystemMaxUse=1G`,
`MaxRetentionSec=1month`).

```bash
journalctl -u holdem-api -f
journalctl -u holdem-worker@0 --since -1h
```

## Install / upgrade / uninstall

```bash
sudo scripts/release/install.sh v1.0.0        # first install
sudo scripts/release/upgrade.sh v1.1.0        # backup → install → flip → health-check
sudo scripts/release/uninstall.sh [--purge]   # remove units/env; --purge also data+releases
```

`upgrade.sh` backs up `solves.db` first, builds the new release, flips
`current`, restarts, and **rolls back automatically** when `/healthz` is not
ok within `HOLDEM_UPGRADE_HEALTH_TIMEOUT` (120 s). The failed release stays in
`releases/` for forensics.

## GPU fault

One worker down: `/healthz` reports `workers.N.up=false` and 503.
`journalctl -u holdem-worker@N` shows the crash; systemd restarts it
(`Restart=always`). The API keeps queueing; solves route to the healthy GPU.
If the GPU itself is lost (NVML can't see it — `gpus.N=false` in /healthz),
`nvidia-smi` on the host tells whether the driver dropped it; after a driver
reload, `systemctl restart holdem-worker@N`.

## Disk full

`/healthz` reports `disk.ok=false` under `HOLDEM_HEALTH_MIN_FREE_BYTES`
(default 1 GiB). Space hogs: `/var/lib/holdem/strategy-*.npz` (deleted 7 days
after completion; pinned jobs excepted — unpin via the API), old backups in
`/var/lib/holdem/backups/`, journald (see Logs). `df -h`, `du -sh
/var/lib/holdem/*`.

## Key rotation

- **Session signing key** (`HOLDEM_SESSION_SECRET` in the env file): generates
  a new HMAC key; **every existing session cookie becomes invalid — all
  browser sessions are logged out**. Edit `/etc/holdem/holdem.env`, then
  `systemctl restart holdem-api`.
- **API key** (`HOLDEM_API_KEY`): same procedure; all scripts and every
  session cookie (signed against the derived secret when
  `HOLDEM_SESSION_SECRET` is unset) are invalidated. Distribute the new key
  out-of-band.
- Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Backup / restore

```bash
scripts/release/backup.sh                 # -> /var/lib/holdem/backups/<ts>/
sudo scripts/release/restore.sh /var/lib/holdem/backups/<ts>
```

Backups hold `solves.db` (jobs + saved spots). Strategy `*.npz`/`*.evs.npz`
files are excluded — they are large and regenerable. A restore onto an
empty data dir brings back job records and saved spots but **not** the
strategies: jobs whose `strategy-*.npz` is gone must be re-solved. For
jobs whose strategy file survived (e.g. a DB-only loss), rerun
`.venv-api/bin/python -m holdem_solver.api.regen_evs --all` (run from
`/opt/holdem/current` with `HOLDEM_API_DATA_DIR=/var/lib/holdem`) to
rebuild the `.evs.npz` companions.

## TLS / reverse proxy (Caddy)

The API binds `127.0.0.1` only; terminate TLS at Caddy with its internal CA:

```caddyfile
# /etc/caddy/Caddyfile
solver.lan {
    tls internal            # private CA: `caddy trust` on each client
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1   # SSE: never buffer event streams
    }
}
```

SSE safety: `flush_interval -1` plus the API's own
`X-Accel-Buffering: no` header keep `/v1/solves/<id>/events` unbuffered.
Cookies: `HOLDEM_SECURE_COOKIES=1` is set in the generated env file, so the
session and CSRF cookies are emitted `Secure; HttpOnly; SameSite=Strict` end
to end through the proxy (verify: `curl -sv https://solver.lan/v1/auth/login`
— the `set-cookie` lines carry all three attributes). If the app is ever
served plain HTTP (no proxy), drop that env var or the browser will refuse
the cookie.

## Driver update

Hold the driver so unattended upgrades don't pull it under a running solver:

```bash
sudo apt-mark hold 'nvidia-driver-*' 'libnvidia-compute-*'
# when updating deliberately:
sudo systemctl stop holdem-solver.target
sudo apt update && sudo apt install --only-upgrade nvidia-driver-XXX
sudo reboot   # driver swaps need a clean module reload
sudo systemctl start holdem-solver.target && curl -fsS localhost:8000/healthz
```
