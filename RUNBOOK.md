# RUNBOOK

Single-host release operations. Layout:

- releases: `/opt/holdem/releases/<version>/`, live one at `/opt/holdem/current`
- secrets/env: `/etc/holdem/holdem.env` (root-only `0600`)
- data (job DB, saved spots, strategies, backups): `/var/lib/holdem`
- sockets: `/run/holdem` (RuntimeDirectory)
- services: `holdem-api.service`, `holdem-worker@0.service`,
  `holdem-worker@1.service`, all under `holdem-solver.target`

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
files are excluded — after a restore onto an empty data dir, rerun
`.venv-engine/bin/python -m holdem_solver.api.regen_evs --all` (run from
`/opt/holdem/current` with `HOLDEM_API_DATA_DIR=/var/lib/holdem`) for jobs
whose strategy file still exists.

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
