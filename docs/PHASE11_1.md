# Phase 11.1 — real install path, correct and verified

Status: complete (VM gate, T1 + fake engine; GPU items are operator-verifies)

Goal: make `scripts/release/install.sh` correct on the real systemd path
and verify it end to end on Devin's VM (no GPUs) with the fake engine.
GPU behaviour is **not** verified here — see "Operator verifies" below.

## 0. Capability probe (Devin VM, 2026-11-18)

```
$ id
uid=1000(ubuntu) gid=1000(ubuntu) groups=1000(ubuntu),27(sudo),109(kvm),998(docker)
$ sudo -n true
(exit 0)
$ ps -p 1 -o comm=
systemd
$ systemctl is-system-running
running
$ docker info --format ...
Server Version: 29.9.2  Storage: overlayfs  Cgroup: systemd v2  OS: Ubuntu 22.04.5 LTS
$ getent group video render
video:x:44:
render:x:110:
$ systemd-detect-virt; systemd-detect-virt -c
kvm
none
$ nvidia-smi -L
bash: line 1: nvidia-smi: command not found
(exit 127)
```

Tier chosen: **T1** (root via passwordless sudo + systemd as PID 1 on a KVM
guest, not a container). T2 (Docker systemd container) is not needed. No
GPU and no NVIDIA driver: the engine is `fake` for every run in this phase.

Tier definitions used by `scripts/release/common.sh::detect_tier` and
printed at the top of every acceptance log:

| tier | condition |
|---|---|
| T1 | root, PID 1 is systemd, not in a container |
| T2 | root, PID 1 is systemd, inside a container (`systemd-detect-virt -c`) |
| T3 | root, no systemd as PID 1 |
| T4 | no root — acceptance-root.sh stops and reports |

## Test cases

Written before implementation; each names its evidence.

### TC1 — interpreter discovery (no `$HOME` defaults)

- Discovery order per interpreter: explicit `HOLDEM_API_PYTHON` /
  `HOLDEM_ENGINE_PYTHON`; then `/opt/holdem/python/**` (uv layout
  `cpython-<ver>-linux-x86_64-gnu/bin/python<maj.min>`); then system paths
  (`/usr/local/bin`, `/usr/bin`). `$HOME`, `/root` and `/run/user` are never
  searched; an interpreter whose real path is under a forbidden prefix FAILs
  (systemd installs; `ProtectHome=yes` hides it).
- Versions asserted: engine exactly `3.9.x`, API exactly `3.15.2`.
- The real path (every component) must be traversable by the service user
  (`runuser -u holdem -- test -x` when root and the user exists; mode bits
  for "other" when the user does not exist yet).
- Every failure names the variable to set and the exact install command
  (`sudo env UV_PYTHON_INSTALL_DIR=/opt/holdem/python <uv> python install --no-bin 3.9`).
- Tests: `tests/test_api_phase11_1.py::test_discovery_explicit_then_root_then_system`, `tests/test_api_phase11_1.py::test_discovery_newest_patch_no_freethreaded`, `tests/test_api_phase11_1.py::test_never_searches_home`, `tests/test_api_phase11_1.py::test_wrong_version_fails_with_fix`, `tests/test_api_phase11_1.py::test_missing_interpreter_fails_with_fix`, `tests/test_api_phase11_1.py::test_home_like_prefix_fails_under_systemd`, `tests/test_api_phase11_1.py::test_untraversable_for_service_user`, `tests/test_api_phase11_1.py::test_sudo_simulation_home_root`.

### TC2 — fake-engine install

- `holdem-worker@.service` runs `--engine ${HOLDEM_SOLVER_ENGINE}`;
  `service.sh` uses the same value from the env file.
- `HOLDEM_INSTALL_ENGINE=real|fake` (default `real`) is written into the
  generated env as `HOLDEM_SOLVER_ENGINE`; the fake engine venv installs
  `requirements/engine-fake.txt`.
- The fake worker never imports `gpu_cfr` or `torch`.
- Preflight checks the `video` and `render` groups; `install.sh` (root)
  creates missing ones with `groupadd --system`.
- Tests: `tests/test_api_phase11_1.py::test_units_and_service_use_env_engine`, `tests/test_api_phase11_1.py::test_install_engine_written_to_env`, `tests/test_api_phase11_1.py::test_fake_requirements_have_no_cuda`, `tests/test_api_phase11_1.py::test_fake_engine_never_imports_gpu_cfr`, `tests/test_api_phase11_1.py::test_groups`.

### TC3 — `install.sh --check`

PASS/FAIL (SKIP when not applicable, with the reason) per item; changes
nothing; no root needed. Items: interpreters, versions, forbidden prefix,
service-user traversal, tools (`rsync npm node curl`), GPU-CFR present +
initialized + readable by the service user (real engine), `nvidia-smi` and
every `HOLDEM_GPUS` id visible (real engine), port free, conflicting
existing units (listed), disk, systemd present, groups. Exit code non-zero
on any FAIL; `install.sh` runs it first and aborts on FAIL.

- Tests: `tests/test_api_phase11_1.py::test_check_all_pass_and_changes_nothing`, `tests/test_api_phase11_1.py::test_gpucfr_checks_real_engine_only`, `tests/test_api_phase11_1.py::test_gpus_real_engine`, `tests/test_api_phase11_1.py::test_port_in_use`, `tests/test_api_phase11_1.py::test_tools_missing`, `tests/test_api_phase11_1.py::test_conflicting_units`, `tests/test_api_phase11_1.py::test_disk_space`, `tests/test_api_phase11_1.py::test_systemd_item`, `tests/test_api_phase11_1.py::test_install_aborts_on_preflight_fail`, `tests/test_api_phase11_1.py::test_install_rejects_bad_engine`.
- Each test breaks exactly one condition and asserts that item FAILs and
  the others do not.

### TC4 — non-systemd smoke runs the interpreter check

- `smoke.sh` runs `install.sh --check` (interpreter items included) before
  installing; its log is named `<ts>-nosys-<engine>.log` and starts with
  the tier/engine header.
- Tests: `tests/test_api_phase11_1.py::test_smoke_runs_preflight_and_logs_tier_engine`, `tests/test_api_phase11_1.py::test_acceptance_root_refuses_without_root`, `tests/test_api_phase11_1.py::test_detect_tier_t4_without_root`.

### TC5 — privileged acceptance (`acceptance-root.sh`)

`sudo scripts/release/acceptance-root.sh --engine fake` on the VM:

1. header: tier, engine, host, git sha, interpreters;
2. `install.sh --check` PASS, then a real install through systemd
   (`/opt/holdem`, `/etc/holdem`, `/var/lib/holdem`, `holdem` user);
3. units active, processes run as `holdem`, worker cmdline carries
   `--engine fake`, hardening properties set;
4. smoke steps against it: `/healthz`, `/version`, login + HttpOnly cookie,
   estimate, a solve on GPU 0 and a solve on GPU 1 (asserted), `/node`,
   SSE reconnect with `Last-Event-ID`, `/ui/` serves the SPA built by
   `npm ci && npm run build`, restore drill (backup, wipe DB, `restore.sh`,
   old job still served);
5. `upgrade.sh` → healthy, old job served; forced-failure upgrade → rolled
   back to the previous release, healthy;
6. `uninstall.sh --purge` → units, env, releases, data gone; interpreters
   under `/opt/holdem/python` kept;
7. log at `scripts/release/smoke-results/<ts>-<tier>-<engine>.log`,
   committed.

### TC6 — cutover rehearsal

Simulated old deployment on the VM: a `holdemold` user running the
Phase 5 *user* units (`holdem-api`, `holdem-worker@0/1`,
`holdem-solver.target`, linger on) from a checkout in its home, data in
`~/.local/share/holdem-solver`, env in `~/.config/holdem-solver/holdem.env`,
API on `:8000`. The only difference from the outpost is the engine flag
(`fake`, no GPUs). `scripts/release/cutover.sh`:

1. identify old units + data dir (from the installed unit file), port;
2. back up (online SQLite backup + unit/env copies);
3. stop and disable the old units;
4. install the release (units not started yet), migrate the DB via
   `restore.sh`, rewrite `strategy_path` to the new data dir, copy
   strategy files, keep the old API key;
5. start, verify `/healthz` and `/node` of a pre-cutover job;
6. `cutover.sh --rollback`: stop/disable the release units, re-enable and
   start the old units, verify old `/healthz` and the same `/node`.

Evidence: `scripts/release/smoke-results/<ts>-<tier>-<engine>-cutover.log`.

### TC7 — docs

- RUNBOOK: Prerequisites (exact commands) + first-install walk-through;
  cutover + rollback section; `regen_evs` uses `.venv-api`.
- PHASE11.md: RUNBOOK location (`RUNBOOK.md`, repo root), smoke log name
  and test counts reconciled; the guard
  (`scripts/phase10/check_suites.py::check_release_docs`) fails when a
  `PHASE11*.md` cites a missing/untracked file path, or a suite count that
  does not match the cited gate log.

## Decisions

- Fake engine venv: `requirements/engine-fake.txt` (numpy, same pin as
  `requirements/engine.txt`). `engine.txt` pins torch/triton CUDA wheels
  that the fake worker never imports; installing them on a GPU-less box
  only costs time and disk. Real installs are unchanged.
- Engine selection lives in the env file (`HOLDEM_SOLVER_ENGINE`), read by
  the worker unit (`--engine ${HOLDEM_SOLVER_ENGINE}`), `service.sh` and
  the API, so one switch moves all three.
- Interpreters for the VM run: `uv python install --no-bin 3.9 3.15.2` into
  `/opt/holdem/python` (3.9.25, 3.15.2), found by discovery (no
  `HOLDEM_*_PYTHON` set).

## Results (Devin VM, tier T1, engine fake)

Bugs found by the first run on the real systemd path (all fixed here):

1. As root, `rsync -a` copied the checkout owner's uid into the release
   tree (not root-owned, contrary to `install.sh`'s comment). Now
   `--no-owner --no-group`; acceptance asserts no non-root file.
2. Forced-failure rollback under systemd: the broken release crash-looped
   into `StartLimitBurst`, so the rollback's `systemctl restart` was refused
   ("Start request repeated too quickly") and the stack stayed down.
   `upgrade.sh` now runs `systemctl reset-failed` before restarting.
3. `service.sh` (non-systemd path) launched `python -m` from the caller's
   cwd, so from a checkout it ran the checkout's code, not the release's
   (`/ui/` 404'd because the checkout had no `ui/dist`). It now `cd`s to
   `current`, like the units' `WorkingDirectory`; smoke asserts the cwd.
4. `scripts/release/smoke-results/` was gitignored, so Phase 11's cited
   smoke log was never committed. Un-ignored; logs committed.

Evidence:

- `scripts/release/smoke-results/20261118-204657-T1-fake.log` —
  `acceptance-root.sh --engine fake` PASS at commit `1f4665d`: preflight
  PASS; install as root through system units; units active as `holdem`,
  interpreters from `/opt/holdem/python`, cwd = release, hardening props,
  worker cmdline `--engine fake --gpu N`, `CUDA_VISIBLE_DEVICES=N`, groups
  `video render`, no `gpu_cfr`/torch/CUDA mapped; login, estimate, solves
  on workers 0 and 1, `/node`, SSE reconnect, `/ui/`, restore drill,
  upgrade, forced-failure rollback (bug 2 fixed), `uninstall --purge`.
  Later commits did not touch the systemd path (`service.sh`, docs, tests).
- `scripts/release/smoke-results/20261118-205006-T1-fake-cutover.log` —
  `cutover-rehearsal.sh` PASS: simulated `holdemold` user-unit deployment
  on :8000, `cutover.sh --dry-run`, cutover (strategy_path rewritten, old
  key kept, pre-cutover `/node` identical), `--rollback` (old units back,
  `/node` identical), teardown.
- `scripts/release/smoke-results/20261118-210342-nosys-fake.log` —
  `smoke.sh --engine fake` PASS (tier nosys: `service.sh`, no units),
  including the interpreter preflight.
- `install.sh --check` on the VM: fake engine all PASS; real engine FAILs
  exactly `gpus` ("nvidia-smi not found"); with
  `HOLDEM_ENGINE_PYTHON` pointing at the home-dir uv interpreter it FAILs
  `prefix.engine` and `traverse.engine` (holdem cannot traverse
  `/home/ubuntu`), each naming the variable and the install command.

## Operator verifies (not verified in this phase)

Run on the outpost: `sudo scripts/release/acceptance-root.sh --engine real`.
None of these were exercised on the VM (no GPU, no driver):

- NVML/GPU visibility from the hardened `holdem-api.service`
  (`/healthz` `gpus.0`/`gpus.1` true);
- `nvidia-smi` sees both GPUs; `install.sh --check` GPU items PASS;
- `gpu_cfr` + torch load under `holdem-worker@.service` with
  `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`,
  `NoNewPrivileges=yes`;
- `/dev/nvidia*` device access via `SupplementaryGroups=video render`;
- real solves land on GPU 0 and GPU 1 (`CUDA_VISIBLE_DEVICES=%i`);
- the real engine venv (`requirements/engine.txt`, CUDA wheels) installs
  from `/opt/holdem/python` 3.9;
- the cutover on the outpost's actual deployment (not run here).
