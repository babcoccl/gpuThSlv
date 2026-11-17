# Architecture

Regenerated from the code on 2026-11-17 (Phase 8.5). The ASCII diagram in
`README.md` is the overview; this file is the reference.

## Components and process model

Three kinds of processes; no imports shared between them beyond
`holdem_solver/protocol` (pure Python, 3.9-safe syntax).

| Process | Runtime | Module | Role |
|---|---|---|---|
| API | Python 3.15 (`.venv-api`) | `holdem_solver.api` | FastAPI app: auth, rate/size limits, SQLite job store, scheduler, `/metrics` |
| Engine worker ×1/GPU | Python 3.9 (`.venv-engine`) | `holdem_solver.engine_worker` | Owns `gpu_cfr` (compiled `.so`); builds trees, runs CFR, exports strategies |
| Workers | spawned (`HOLDEM_WORKER_MODE=spawn`, default; `--parent-pid` watchdog) or external systemd units (`external`; API SIGKILLs on hang and waits for restart) | | pinned by `CUDA_VISIBLE_DEVICES` |

Transport: JSON-RPC 2.0 over a Unix socket per worker (dir mode 0700);
bulk arrays go through `.npy` files in `/dev/shm` (dir 0700, files 0600,
paths must resolve inside it). Message cap `MAX_MESSAGE_BYTES` in
`protocol/messages.py`.

## RPC methods (worker surface, `engine_worker/service.py`)

`rpc_ping` (state/vram/pid/built/dtype) · `rpc_build(game, options)` →
`describe()` (game, num_nodes, num_infosets, num_infoset_actions,
estimated_vram_bytes, device, **dtype**, spot) · `rpc_estimate_spot(spot,
dtype)` → node count + VRAM estimate without building ·
`rpc_solve(**params)` starts a solve job · `rpc_solve_wait(timeout,
job_id)` · `rpc_progress` · `rpc_cancel` · `rpc_get_strategy` ·
`rpc_reset` · `rpc_close` · `rpc_shutdown`.

Error codes (`protocol/messages.py`): JSON-RPC standard −32700/−32600/
−32601/−32602/−32603 plus `ERROR_ENGINE` −32000, `ERROR_NOT_BUILT` −32001,
`ERROR_BUSY` −32002, `ERROR_NO_JOB` −32003, `ERROR_OOM` −32004.

## Engine worker internals

- `SolverEngine` (`solver.py`) wraps `StagewiseCFRSolver`: build →
  `_make_config` (`variant` cfr_plus default, `dtype`, `device`) → solver.
  Reports `device`, `dtype` (from `config.device.dtype`), runs
  `solve(target_exploitability=...)` with progress callbacks, exports the
  average strategy flat (compiled-game infoset order — *not*
  `spec.infosets` order).
- `RealEngine` (`real.py`) adds `build(SpotConfig-dict)` → `build_spot` +
  `apply_weights`, `estimate_spot`, `describe`.
- `FakeEngine` (`fake.py`) mirrors the surface for GPU-free API tests.
- `spot_builder.py` picks the builder: vendored `gpu_cfr` river/turn games
  for plain spots; `tree_builder.py` (custom raise-capable builder) when
  `needs_custom_builder` — `raise_cap > 0` or a mapping `bet_sizes`.
  `tree_builder` also handles per-player/per-street size sets, all-in, and
  dead cards (river only).
- `memory.py` — `estimate_vram_bytes` (~322 B/node calibration).

## API internals

- `app.py` — endpoints: `GET /healthz` (unauthenticated), `GET /metrics`,
  `GET /v1/gpus`, `POST /v1/solves` (202; `Idempotency-Key`),
  `GET /v1/solves/{id}`, `GET /v1/solves/{id}/strategy`
  (`view=infosets|hand_grid`, `player=`, `history=`),
  `DELETE /v1/solves/{id}`. Phase 9 adds `GET /v1/solves/{id}/tree`
  (node navigation), `GET /v1/solves/{id}/node` (frequencies, per-action
  EVs, equity, ranges), `GET /v1/solves/{id}/events` (SSE,
  `Last-Event-ID` replay), `POST /v1/solves/{id}/pin|unpin`,
  `/v1/spots` CRUD + `duplicate` + `{id}/solve`, and
  `POST /v1/auth/login|logout` (HttpOnly SameSite=strict session cookie +
  CSRF token; CORS off unless `HOLDEM_CORS_ORIGINS`).
- `nodes.py` — loads a strategy npz into a `protocol/evwalk` `TreeIndex`;
  `tree_node` (children summary, chance-node dealt cards),
  `node_values` (per hand class / per combo frequencies, per-action EVs,
  hand EV, equity, ranges).
- `models.py` — `SolveRequest` (SpotConfig fields incl. `bet_sizes`
  str|list|dict, `all_in`, `dead_cards`, `raise_cap`, `raise_sizes`;
  solver params `variant`, `dtype`, stop conditions, `priority`,
  `timeout_seconds`) and response models.
- `scheduler.py` — dispatcher thread, one job per GPU, priority queue in
  SQLite, VRAM admission (estimate × factor; factor seeded 1.6 until ≥3
  qualifying samples, then worst measured peak/estimate ×1.05), heartbeat
  watchdog (`HOLDEM_HEARTBEAT_SECONDS`, `HOLDEM_HUNG_WORKER_SECONDS`),
  strategy retention.
- `store.py` — SQLite `jobs` and `spots` tables; queued jobs and saved
  spots survive restarts (in-flight jobs fail `api_restart`). `pinned`
  solves are skipped by strategy retention. `gpus.py`, `metrics.py`
  (Prometheus), `security.py` (Bearer key, session cookies, CSRF, token
  buckets, body cap), `strategy.py` (infosets + reach-weighted
  `hand_grid`), `vram.py`, `examples.py`, `client/` (`engine_client.py`
  JSON-RPC client, `process.py` spawn/attach).

## Job lifecycle

`queued → building → solving → done | failed | cancelled`. Job
`error_code` values: `cuda_oom`, `worker_hung`, `worker_died`, `timeout`,
`disk_full`, `api_restart`, `engine_error`, `vram` (admission). Strategies
persist to `<data_dir>/strategy-<id>.npz`; deleted after
`HOLDEM_STRATEGY_RETENTION_SECONDS` (7 d) → `/strategy` returns 410.

## Metrics

`GET /metrics` (Prometheus): `holdem_jobs{status}`,
`holdem_jobs_failed{error_code}`, `holdem_queue_depth`,
`holdem_gpu_*` (NVML util/VRAM), `holdem_build_duration_seconds`,
`holdem_solve_duration_seconds`, `holdem_iterations_per_second`,
`holdem_vram_factor{source}`, `holdem_worker_restarts_total`,
`holdem_gpu_busy`, `holdem_worker_healthy`.

## Module map

```
holdem_solver/
  protocol/    spot.py (SpotConfig/validation), cards.py, messages.py
               (JSON-RPC framing + error codes), paths.py,
               evwalk.py (pure-Python strategy-tree walk: reaches, EVs,
               equity, rank7 showdown — mirrors scripts/phase7/texas_dump)
  engine_worker/  solver.py, real.py, fake.py, service.py (RPC dispatch),
                  spot_builder.py, tree_builder.py, memory.py
  api/         app.py, models.py, scheduler.py, store.py, strategy.py,
               nodes.py, gpus.py, metrics.py, security.py, vram.py,
               examples.py
  client/      engine_client.py, process.py, errors.py
scripts/
  phase7|8|8_5|8_6/  TexasSolver comparison harnesses + run artifacts
  phase9/          UI-backend demo + validation (curl logs, ev/tree gates)
  run_all_tests.sh  both-venv test gate; check_links.py
deploy/      systemd user/system units, env template, docker-compose
```
