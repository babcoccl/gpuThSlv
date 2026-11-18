# Phase 11 — native single-host release (Ubuntu, systemd)

Private use only; the installed system is never redistributed. The install
expects the operator's own GPU-CFR copy at the configured submodule path
(`third_party/GPU-CFR`, see `docs/LICENSES.md`).

Release layout:

```
/opt/holdem/releases/<version>/   # one directory per installed version
/opt/holdem/current -> releases/<version>
/etc/holdem/holdem.env            # root-only 0600 secrets (never overwritten)
/var/lib/holdem/                  # job DB + strategy files (state survives upgrades)
/run/holdem/                      # RuntimeDirectory: worker sockets, shm manifests
```

Services: `holdem-api.service` (API, binds 127.0.0.1) and
`holdem-worker@<gpu>.service` (one instance per GPU; `CUDA_VISIBLE_DEVICES`
comes from the instance name). Both under `holdem-solver.target`, running as
the unprivileged `holdem` user.

`scripts/release/`:

| script | purpose |
|---|---|
| `install.sh` | build `releases/<version>` (both venvs from pinned requirements, `npm ci` + `npm run build` for `ui/dist`), create user/dirs/env, install units, flip `current`, restart, health-check |
| `upgrade.sh` | `install.sh` onto the existing tree: DB backup, symlink flip, restart, automatic rollback to the previous release if `/healthz` does not go healthy |
| `uninstall.sh` | stop units, remove units and (with `--purge`) the env file, releases and `/var/lib/holdem` |
| `backup.sh` | timestamped copy of `solves.db` + spots into `/var/lib/holdem/backups/` (strategy files excluded — regenerated via `regen_evs`) |
| `restore.sh` | stop API, restore a backup into the data dir on a release copy, restart |
| `smoke.sh` | acceptance: fresh install in a throwaway prefix/user, login, estimate, solve on both GPUs, `/node`, SSE reconnect, UI loads; then upgrade and rollback |

`HOLDEM_PREFIX` / `HOLDEM_DATA_DIR` / `HOLDEM_CONF_DIR` / `HOLDEM_RUN_DIR`
override `/opt/holdem`, `/var/lib/holdem`, `/etc/holdem`, `/run/holdem` so the
smoke run never touches the real install; `HOLDEM_NO_SYSTEMD=1` runs the API
and workers directly instead of installing units.

## Test cases

Written before implementation; each names its evidence.

### T1 — third-party hygiene

- `git ls-files` contains no `*.so`, `*.zip`, vendored binary, or
  TexasSolver file. The only third-party entry is the `third_party/GPU-CFR`
  gitlink (submodule; its contents are not tracked by this repository).
- `docs/LICENSES.md` exists, lists every Python dependency (both venvs,
  via `pip-licenses`), every npm dependency (via `license-checker`),
  GPU-CFR, TexasSolver and the CUDA/driver stack, each with a license
  string or "none found" and a role (runtime / validation only / dev only),
  and states plainly: GPU-CFR has no license file upstream (a CC BY-NC-SA
  badge only), this system is private-use and not redistributed, and the
  install expects the user's own GPU-CFR copy at the configured path.
- Test: `tests/test_api_phase11.py::test_no_vendored_binaries`,
  `test_licenses_doc_complete` (required strings present).

### T2 — install

- `install.sh` into `HOLDEM_PREFIX=$(mktemp -d)` creates
  `releases/<version>/{.venv-api,.venv-engine,ui/dist}`, a `current`
  symlink, the env file at `$HOLDEM_CONF_DIR/holdem.env` mode `0600`
  containing fresh `HOLDEM_API_KEY` and `HOLDEM_SESSION_SECRET`, and the
  data dir.
- Re-running `install.sh` is idempotent: existing env file is untouched
  (keys preserved), `current` still points at the new release.
- The generated `holdem.env` never overwrites existing secrets: run twice,
  keys identical.
- Unit files render with the install paths; `systemd-analyze verify` passes
  when systemd is available (skipped otherwise).
- Test: `tests/test_api_phase11.py::test_install_idempotent_stub` (shells out to a
  scratch prefix with `HOLDEM_STUB_VENVS=1` stub pip installs so it runs in
  CI time) + the real run in `smoke.sh` (T6).

### T3 — systemd + /healthz

- `holdem-api.service` `After=`/`Wants=` the worker units;
  `holdem-worker@.service` sets `CUDA_VISIBLE_DEVICES=%i`, runs
  `Restart=always` with `TimeoutStopSec` for graceful shutdown,
  `RuntimeDirectory=holdem`, hardening (`NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`), journald output.
- `deploy/systemd/holdem-journald.conf` caps journal retention
  (`SystemMaxUse`, `MaxRetentionSec`).
- `GET /healthz` (unauthenticated) returns `200` with
  `{status: "ok", workers: {…up}, gpus: {…visible}, disk: {…}}` when every
  configured GPU's worker answers ping, NVML sees the device, and the data
  dir has ≥ `HOLDEM_HEALTH_MIN_FREE_BYTES` free; `503` with
  `status: "degraded"` and the failing checks otherwise.
- `GET /version` returns `{version, git_sha}`.
- Test: `test_healthz_degraded_when_worker_down`,
  `test_healthz_ok_reports_workers_gpus_disk`,
  `test_healthz_degraded_when_disk_full`, `test_version_endpoint`.

### T4 — TLS/proxy and auth

- `RUNBOOK.md` (repo root) documents the Caddy config (internal CA via
  `tls internal`), with `flush_interval -1` / `X-Accel-Buffering` handling
  so SSE stays unbuffered, and `HOLDEM_SECURE_COOKIES=1` in the env file so
  cookies are `Secure; HttpOnly; SameSite=Strict` end to end through the
  proxy.
- Login is rate-limited per IP (10/min, burst 5 — already implemented);
  tests hit `/v1/auth/login` past the burst and see 429.
- RUNBOOK documents session-key rotation (`HOLDEM_SESSION_SECRET` change →
  every cookie invalid → all users logged out) and API-key rotation.
- Test: `test_login_rate_limited`, cookie attribute assertions
  (`test_login_sets_secure_httponly_samesite` with
  `HOLDEM_SECURE_COOKIES=1`).

### T5 — data safety

- `backup.sh` produces `<backups>/<ts>/solves.db` (SQLite online backup)
  plus a manifest; strategy `*.npz`/`*.evs.npz` files are excluded.
- `restore.sh` onto a copy: API serves `/v1/solves/<old id>/node` for a
  job restored from backup.
- Restore drill in `smoke.sh`: solve a job, backup, wipe data dir, restore,
  fetch `/node` for the old job id.
- Test: `test_backup_restore_roundtrip` (runs the scripts against a scratch
  data dir; no GPU needed — a `done` job row + strategy file is planted).

### T6 — acceptance (`smoke.sh`)

Against a throwaway prefix + user, all recorded into
`scripts/release/smoke-results/<ts>-<tier>-<engine>.log` (named so in
Phase 11.1; see `docs/PHASE11_1.md`):

1. fresh install; `/healthz` ok; `/version` answers;
2. login → cookie; `POST /v1/solves` estimate+solve on both GPUs
   (`HOLDEM_GPUS=0,1`); poll to `done`;
3. `GET /v1/solves/<id>/node` returns EVs;
4. SSE: connect to `/events`, reconnect with `Last-Event-ID` after the
   first progress event — the stream resumes (snapshot then live);
5. `GET /ui/` returns the SPA;
6. upgrade: run `upgrade.sh` with a new version → health ok, old job still
   served;
7. rollback: force a health failure → previous release restored.
8. Re-run `npm run e2e:real` and the scale run; `regen_evs --all` on the
   outpost (counts recorded); first-call latency recorded for a declined
   `evs.npz` (delete a job's evs file, time the first `/node`).

### Gate

- `scripts/run_all_tests.sh` green.
- `smoke.sh` passes on fresh install, after upgrade and after rollback.
- `docs/LICENSES.md` complete with unknowns stated.
- Evidence guard (`tests/test_doc_evidence.py`) passes.

### Results (2026-11-18 run on the outpost)

- `scripts/release/smoke.sh` — PASS on the outpost (fresh install, login,
  estimate, two solves on GPUs 0+1, `/node`, SSE reconnect, `/ui/`, restore
  drill, upgrade healthy, forced-failure rollback healthy). Smoke ran with
  `HOLDEM_NO_SYSTEMD=1` (no root in the session): `service.sh` drove the
  same unit layout; system units were rendered by `install.sh` and
  syntax-checked in tests, not run. *Reconciled in Phase 11.1:* the log
  this line originally cited was never committed (`smoke-results/` was
  gitignored); logs are now committed and named
  `<ts>-<tier>-<engine>.log`, and the real systemd path was first run in
  Phase 11.1 (T1, fake engine — `docs/PHASE11_1.md`).
- `npm run e2e:real` — PASS (login → estimate → solve on both GPUs →
  strategy viewer → spot library).
- Scale run `node scripts/scale-e2e.mjs` — committed artifact
  `scripts/phase10/logs/scale-10-2.json` (river 573,031 nodes in 9 s,
  turn-chance 442,891 in 24 s, raisecap-2 95,743 in 3 s; ttfr 261/95/80 ms;
  ui_errors 0); `docs/PHASE10_2.md` tables updated to this run's numbers.
- `python -m holdem_solver.api.regen_evs --all` on the outpost data dir —
  regenerated=1373 skipped=1.
- First `/node` on a declined `evs.npz` (strategy-c5f4a72a2bd2, 442,891
  nodes; file moved aside, API restarted, in-memory compute): first call
  16.65 s, warm call 2 ms. The evs file was restored afterwards.
- `scripts/run_all_tests.sh` — green on the outpost. *Reconciled in
  Phase 11.1:* the suite counts first written here had no committed gate
  log behind them and were removed; current counts and their committed log
  are in `docs/PHASE11_1.md`.
