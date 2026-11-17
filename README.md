# gpuThSlv

GPU-accelerated heads-up no-limit Texas Hold'em CFR solver, exposed as a REST
API. Two runtimes: a Python 3.15 API process (`.venv-api`) and one Python 3.9
engine worker per GPU (`.venv-engine`), talking JSON-RPC over Unix sockets.
See `AGENTS.md`, `docs/PROGRESS.md` and `docs/BENCHMARKS.md`.

## Architecture

```
                 HTTPS/HTTP (Bearer key)            Prometheus
 clients ───────────────┐                          scraper
                        ▼                            │ GET /metrics
 ┌──────────────────────────────────────────────────┴────────┐
 │ API  .venv-api  Python 3.15  (holdem_solver.api)           │
 │  FastAPI ─ auth, POST rate limit, body-size limit          │
 │  SQLite job store (queue, priorities, idempotency)         │
 │  Scheduler: VRAM admission, watchdog, timeouts, retention  │
 └───────┬───────────────────────────────────────┬────────────┘
         │ JSON-RPC, Unix socket (0700 dir)       │
         │ bulk arrays: .npy in /dev/shm (0700)   │
 ┌───────▼──────────────────┐        ┌───────────▼─────────────┐
 │ worker GPU 0  Python 3.9  │        │ worker GPU 1  Python 3.9 │
 │ CUDA_VISIBLE_DEVICES=0    │        │ CUDA_VISIBLE_DEVICES=1   │
 │ holdem_solver.engine_worker│       │ holdem_solver.engine_worker│
 │   └─ gpu_cfr (.so, cp39)  │        │   └─ gpu_cfr (.so, cp39) │
 └───────────────────────────┘        └──────────────────────────┘
        RTX 5060 Ti 16 GB                    RTX 5060 Ti 16 GB
```

Workers are either spawned by the API (`HOLDEM_WORKER_MODE=spawn`, default:
they get `--parent-pid` and exit if the API dies) or run as their own
systemd units (`external`: the API attaches, and on a hang SIGKILLs the
worker and waits for systemd to restart it). Only
`holdem_solver/engine_worker/` imports `gpu_cfr`.

## Game rules and limits

- **Streets:** river spots (5-card `board`) and turn spots (4 cards; the
  river is dealt out). No flop or preflop builder.
- **Players:** heads-up only. `range0` is OOP (acts first each street),
  `range1` is IP. Ranges use `CLASS` tokens — `QQ+`, `AKs`, `AKo`, or
  `CLASS:weight` for weighted ranges; `*` selects all 1326 combos.
- **Actions:** fold, check/call, bet, and raise. `raise_cap` bounds raises
  per street (0 = bet/check/call/fold only); `raise_sizes` lists the raise
  options as `["x", m]` (raise to m× the bet faced) or `["p", f]` (bet
  level + f× the pot after calling). OOP river leads after a call-ended
  turn are allowed (donk bets).
- **Bet sizes:** `bet_sizes` is a preset name (`"standard"`, …), one list
  of pot fractions for every node, a per-player dict `{"0": [...],
  "1": [...]}`, or — on turn spots — a per-street dict
  `{"turn": [...], "river": [...]}`. `all_in` adds the remaining stack as
  a size; `dead_cards` removes cards from river deals.
- **Sizing semantics:** all sizes are fractions of the pot at the decision
  node. The tree keeps fractions exact (no chip rounding).
- Heads-up only; `max_nodes` (2M default) and the VRAM admission check bound
  the spot size (a full-range 9.6M-node river fits; see
  `docs/BENCHMARKS.md`).

## Licensing caveat (GPU-CFR)

The engine is the upstream [lbn187/GPU-CFR](https://github.com/lbn187/GPU-CFR)
project, vendored as a submodule and shipped by upstream as compiled `.so`
files only. GitHub reports no license file for that repository, but its README
carries a **CC BY-NC-SA 4.0** badge (non-commercial, share-alike). This
repository's code does not change those terms: check upstream's terms (and ask
the author if in doubt) before any commercial use or redistribution of the
engine or images that contain it. This note is not legal advice.

## REST API

Start it (requires an API key and both GPUs free):

```bash
export HOLDEM_API_KEY=change-me
export HOLDEM_GPUS=0,1
.venv-api/bin/python -m holdem_solver.api        # http://127.0.0.1:8000
```

Docs UI at `/docs`; unauthenticated `GET /healthz` for liveness.

Submit a solve — the body is a `SpotConfig` plus solver params:

```bash
curl -X POST http://127.0.0.1:8000/v1/solves \
  -H "Authorization: Bearer $HOLDEM_API_KEY" -H 'Content-Type: application/json' \
  -d '{
    "board": "KsJsTh7d2c",
    "range0": "QQ+, AKs",
    "range1": "TT, AQ",
    "pot": 20, "stack": 100, "bet_sizes": "standard",
    "variant": "cfr_plus",
    "iterations": 500, "progress_every": 100
  }'
# -> 202 {"id": "..."}
```

Stop conditions: `iterations`, `time_budget` (seconds), or
`target_exploitability` — which must also carry `time_budget` or
`max_iterations` as a cap. `solver_options` passes extra engine solver options.

```bash
curl -H "Authorization: Bearer $HOLDEM_API_KEY" http://127.0.0.1:8000/v1/solves/<id>
# status, iteration, exploitability (+ history), elapsed, gpu, nodes,
# estimated and measured VRAM, error

curl -H "Authorization: Bearer $HOLDEM_API_KEY" http://127.0.0.1:8000/v1/solves/<id>/strategy
# per-infoset action frequencies mapped to hand names and action labels

curl -X DELETE -H "Authorization: Bearer $HOLDEM_API_KEY" http://127.0.0.1:8000/v1/solves/<id>
# cancel

curl -H "Authorization: Bearer $HOLDEM_API_KEY" http://127.0.0.1:8000/v1/gpus
# worker state + pynvml memory/utilization per GPU
```

`dtype` (top-level, `float32` default or `float64`) sets solver precision and
is echoed in every status. Status also carries `exploitability_iteration`,
the iteration at which the reported `exploitability` was measured.
`GET /v1/solves/<id>/strategy?view=hand_grid` returns, per decision point,
the 169 hand classes (AA, AKs, … AKo, … 22) with combo-averaged action
frequencies; add `player=0|1` and `history=check,b10` (`history=` for the
root) to pick one node. The default `view=infosets` is unchanged. Every
endpoint has a request/response example in `/docs`.

### Tree navigation and per-node values (Phase 9)

Read endpoints for building a UI — no client-side history parsing:

```bash
curl -H "Authorization: Bearer $HOLDEM_API_KEY" \
  "http://127.0.0.1:8000/v1/solves/<id>/tree?history=check,b10"
# node: player, street, pot, stacks, board (dealt cards folded in),
# canonical actions + labels, children summary; is_chance + dealt_cards
# on turn river-deal nodes.

curl -H "Authorization: Bearer $HOLDEM_API_KEY" \
  "http://127.0.0.1:8000/v1/solves/<id>/node?history=check,b10&combos=true"
# per hand class (per combo with combos=true): reach-weighted action
# frequencies, per-action EVs, hand EV, equity vs the opponent's reaching
# range, and both players' reaches at the node. Formulas in
# docs/PHASE9.md; EVs validated against the Phase 7 dump_ev walk on
# 6 cap-0 spots (1,756 values, max |Δ| = 7.993605777301127e-15) —
# scripts/phase9/runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json,
# docs/VALIDATION.md.

curl -N -H "Authorization: Bearer $HOLDEM_API_KEY" \
  http://127.0.0.1:8000/v1/solves/<id>/events
# server-sent events: snapshot/progress/done with status, iteration,
# exploitability. Contract (Phase 9.1):
#  - every connection starts with a `retry:` field then a `snapshot`
#    event carrying the CURRENT job state — never skipped;
#  - `Last-Event-ID: <boot>-<seq>` still inside the replay buffer
#    replays the buffered events after it in order, then goes live;
#  - stale, unknown, malformed, evicted, future, or pre-restart ids —
#    and finished jobs — get the snapshot then live events (or the
#    terminal `done` event), no replay;
#  - `: keep-alive` comment heartbeats; `Cache-Control: no-cache` and
#    `X-Accel-Buffering: no` disable proxy buffering.
# Client rule: the snapshot replaces local state; progress events with an
# iteration lower than the current one are ignored; done is terminal.
```

### Saved spots

```bash
curl -X POST -H "Authorization: Bearer $HOLDEM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"dry-river","spot":{...},"tags":["river"],"notes":"..."}' \
  http://127.0.0.1:8000/v1/spots
# 201 -> {"id": "..."}. GET /v1/spots[?tag=...], GET/PATCH/DELETE
# /v1/spots/<id>, POST /v1/spots/<id>/duplicate,
# POST /v1/spots/<id>/solve (queues a solve with the usual params).

curl -X POST -H "Authorization: Bearer $HOLDEM_API_KEY" \
  http://127.0.0.1:8000/v1/solves/<id>/pin
# pinned solves are never removed by strategy retention.
```

Limits: 512 spots, name ≤ 200 chars, notes ≤ 2000 chars, ≤ 32 tags × ≤ 64
chars.

### Browser auth

`POST /v1/auth/login` with `{"key": "<api key>"}` sets an HttpOnly
`SameSite=strict` session cookie (12 h) plus a readable `holdem_csrf`
cookie (also in the body). Cookie-authenticated GETs work as-is; mutating
requests must echo the CSRF token in `x-csrf-token` (403 otherwise).
`POST /v1/auth/logout` clears both. CORS is off by default — set
`HOLDEM_CORS_ORIGINS` to allow browser origins. Login attempts are
rate-limited (10/min, burst 5). The Bearer API key still works for
scripts; a request that supplies both is a script call (no CSRF needed).
The login credential is the API key (`{"key": ...}`). Session cookies are
stateless HMAC tokens (`v1.<expiry>.<sig>`) signed with
`HOLDEM_SESSION_SECRET` (or a sha256 of `"holdem-session:" + api_key` when
unset), so they **survive an API restart** as long as the signing material
is unchanged — tested in `tests/test_api_sse.py`/`test_api_phase9.py`.
Changing the key or the secret invalidates every live session.

### API usage

Every request below is a complete `POST /v1/solves` body (headers as above).
Stop conditions are the same for all; only `iterations` is shown.

**Standard river spot** — 5-card board, preset bet sizes:

```json
{
  "board": "KsJsTh7d2c",
  "range0": "QQ+, AKs",
  "range1": "TT+, AQs+, AKo",
  "pot": 20, "stack": 100,
  "bet_sizes": "standard",
  "variant": "cfr_plus",
  "iterations": 500
}
```

**Weighted-range spot** — `CLASS:weight` tokens on both players:

```json
{
  "board": "Ks7h2d5c9s",
  "range0": "AKo:0.5, QQ:0.75, 22",
  "range1": "AKo:0.25, JJ:0.5, TT",
  "pot": 20, "stack": 100,
  "bet_sizes": [0.5, 1.0],
  "iterations": 500
}
```

**Raises spot** — `raise_cap` + `raise_sizes` (`["x", m]` = raise to m× the
bet faced; `["p", f]` = bet level + f× the pot after calling):

```json
{
  "board": "Ks7h2d5c9s",
  "range0": "AA, KK",
  "range1": "QQ, JJ",
  "pot": 20, "stack": 100,
  "bet_sizes": [0.5],
  "raise_cap": 1,
  "raise_sizes": [["x", 3.0], ["p", 1.0]],
  "iterations": 500
}
```

**Per-player bet sizes** — `bet_sizes` as a `{"0": ..., "1": ...}` dict
(`"0"` = OOP):

```json
{
  "board": "Ts9s8sJh2c",
  "range0": "AA:0.75, KK:0.5",
  "range1": "QQ:0.5, JJ:0.75",
  "pot": 20, "stack": 100,
  "bet_sizes": {"0": [0.33, 0.5, 1.0], "1": [0.5, 0.75, 1.5]},
  "iterations": 500
}
```

**Turn spot** — 4-card board; per-street `bet_sizes`
(`{"turn": ..., "river": ...}`):

```json
{
  "board": "Ks7h2d5c",
  "range0": "AA, KK",
  "range1": "QQ, JJ",
  "pot": 10, "stack": 30,
  "bet_sizes": {"turn": [0.5], "river": [0.5, 1.0]},
  "iterations": 500
}
```

Spots over `max_nodes` (default 2M), or whose predicted VRAM peak exceeds
14 GiB or every GPU, get 422. The prediction is estimate × factor: the factor
is seeded at **1.6** and stays there until at least **3** finished jobs of that
dtype with an estimate ≥ 64 MiB exist; then it is the worst measured
peak/estimate ratio × 1.05. Why: Phase 4 measured a 153k-node spot peaking at
98 MiB against a 62.7 MiB estimate (×1.56), so the old 1.15 seed under-admitted
by ~35% until a sample arrived; one sample is too few to trust, and small spots
are dominated by fixed allocator overhead, so they are ignored. One job runs
per GPU at a time.

Operations (Phase 4, `docs/PHASE4.md`):

- `"priority"` (higher first) and `"timeout_seconds"` in the body.
  `Idempotency-Key: <key>` header: a retry returns the same job, and a
  different body with the same key gets 409.
- Over `HOLDEM_MAX_QUEUE_DEPTH` (64) queued jobs → 429 with `Retry-After`.
- Failed jobs carry `error_code`: `cuda_oom`, `worker_hung`, `worker_died`,
  `timeout`, `disk_full`, `api_restart`, `engine_error`, …
- Watchdog: `HOLDEM_HEARTBEAT_SECONDS` (5) and `HOLDEM_HUNG_WORKER_SECONDS`
  (120). Strategies are deleted after `HOLDEM_STRATEGY_RETENTION_SECONDS` (7 d;
  `/strategy` → 410). Per-GPU headroom is `HOLDEM_VRAM_RESERVE_BYTES` (512 MiB).
- On restart, queued jobs survive; jobs that were building or solving are
  marked failed (`api_restart`) and not replayed.

## Metrics

`GET /metrics` (Prometheus text format, same auth as `/v1`):

| metric | labels | meaning |
| --- | --- | --- |
| `holdem_jobs` | `status` | jobs in the store by status |
| `holdem_jobs_failed` | `error_code` | failed jobs by error code |
| `holdem_queue_depth` | | queued jobs |
| `holdem_gpu_utilization_ratio`, `holdem_gpu_memory_utilization_ratio` | `gpu` | NVML utilization (0–1) |
| `holdem_gpu_memory_used_bytes`, `holdem_gpu_memory_total_bytes` | `gpu` | NVML VRAM |
| `holdem_build_duration_seconds` | `gpu`, `dtype` | histogram of tree builds |
| `holdem_solve_duration_seconds` | `gpu`, `status` | histogram of solve time per finished job |
| `holdem_iterations_per_second` | `gpu` | last measured CFR rate |
| `holdem_vram_factor`, `holdem_vram_factor_samples` | `dtype`, `source` | VRAM factor in use (`seed`/`measured`) |
| `holdem_worker_restarts_total` | `gpu` | worker respawns (OOM, crash, hang) |
| `holdem_gpu_busy`, `holdem_worker_healthy` | `gpu` | 0/1 |

## Security

- **API key**: `HOLDEM_API_KEY`, sent as `Authorization: Bearer <key>` or
  `X-API-Key`; compared in constant time (`hmac.compare_digest`). Every
  endpoint except `/healthz` requires it; unset = auth off (a warning is
  logged; dev only). Keep it in the 0600 env file, never in the unit or repo.
- **Rate limit**: token bucket per key (or client IP when there is no key) on
  `POST /v1/solves`: `HOLDEM_POST_RATE_PER_MINUTE` (120), burst
  `HOLDEM_POST_BURST` (30); over it → 429 + `Retry-After`. `0` disables.
- **Request size**: bodies over `HOLDEM_MAX_BODY_BYTES` (64 KiB) → 413, checked
  on `Content-Length` and while streaming; ranges are capped at 4096 chars.
- **Socket / shm**: the socket directory and the `/dev/shm` array directory
  are created 0700 and must be owned by the service user (a pre-existing
  directory with other owner or mode is refused); the worker runs with umask
  077, so sockets and `.npy` files are 0600; array paths in RPCs must resolve
  inside that directory. Units add `UMask=0077`.
- **Strategy endpoint** exposes solver output only: the average strategy of
  the job's own spot (hand, action label, frequency). No file paths, other
  jobs, or server state; job ids are random 48-bit tokens and need the key.
- Bind to `127.0.0.1` (default) and put TLS in a reverse proxy if the API
  must be reachable from other hosts.

## Deployment

### systemd (no sudo)

```bash
uv venv .venv-api --python 3.15.2 && uv pip install --python .venv-api/bin/python -r requirements/api.txt
uv python install 3.9 && uv venv .venv-engine --python 3.9 \
  && uv pip install --python .venv-engine/bin/python -r requirements/engine.txt
deploy/install-user-units.sh     # user units + env file + linger, starts holdem-solver.target
systemctl --user status holdem-api holdem-worker@0 holdem-worker@1
journalctl --user -u holdem-api   # unit events; app logs in ~/.local/state/holdem-solver/logs
```

Units (`deploy/systemd/user/`): `holdem-worker@.service` (one per GPU, Python
3.9, `Restart=always`), `holdem-api.service` (Python 3.15,
`HOLDEM_WORKER_MODE=external`, `Restart=always`), `holdem-solver.target`,
and an hourly `holdem-logrotate.timer` (`deploy/logrotate/holdem-solver`:
daily or 100 MB, 14 kept, compressed). `deploy/systemd/system/` has the
root-installed equivalents (`User=holdem`, `/opt/holdem-solver`,
`/etc/holdem-solver/holdem.env`, `/run/holdem-solver`, hardening; logs go to
the journal, `journalctl -u holdem-api -u 'holdem-worker@*'`). The env
template is `deploy/holdem.env.example`.

Windows / PowerShell (to reach a remote box's API):

```powershell
$env:HOLDEM_API_KEY = "<key>"
Invoke-RestMethod http://gpu-box:8000/v1/gpus -Headers @{Authorization = "Bearer $env:HOLDEM_API_KEY"}
```

### Docker

```bash
echo "HOLDEM_API_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" > .env
docker compose up -d --build     # needs the NVIDIA Container Toolkit (runtime: nvidia)
curl localhost:8000/healthz
```

```powershell
# Docker Desktop (WSL2 backend with NVIDIA GPU support)
"HOLDEM_API_KEY=$([guid]::NewGuid().ToString('N'))" | Out-File -Encoding ascii .env
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/healthz
```

One image holds both runtimes (`python:3.15.2-slim` for the API, uv's
CPython 3.9 + torch cu128 for the workers). In the container the API spawns
its workers (spawn mode), so they share the container's `/dev/shm`
(`shm_size: 2gb`) and socket dir. Logs go to Docker's json-file driver,
rotated at 50 MB × 5. The image is ~11 GB (torch cu128 + CUDA wheels for the engine; the API
has no torch); check free disk before building.

## Operations runbook

**OOM** (`error_code=cuda_oom`, `holdem_worker_restarts_total` rises): the
worker is restarted and the job fails; the queue keeps going. The measured
peak still trains the VRAM factor, so retries of the same spot are predicted
higher and rejected (422) if they cannot fit. Check `nvidia-smi` for foreign
processes on that GPU; shrink ranges or `bet_sizes`, or use `float32`.
Lower `HOLDEM_API_MAX_VRAM_BYTES` or raise `HOLDEM_VRAM_RESERVE_BYTES` if OOMs
repeat.

**Hung worker** (`error_code=worker_hung`, a GPU stuck `busy`): the watchdog
pings every `HOLDEM_HEARTBEAT_SECONDS`; after `HOLDEM_HUNG_WORKER_SECONDS`
without an answer it SIGKILLs the worker, fails the job and restarts (spawn
mode) or waits for systemd (external). By hand:
`systemctl --user kill -s SIGKILL holdem-worker@0` (systemd restarts it within
2 s), or `systemctl --user restart holdem-solver.target`. Jobs that were running
when the API restarts become `api_restart`; queued jobs are kept.

**Disk full** (`error_code=disk_full`): the strategy write is atomic (tmp +
rename), so no partial file is left; the job fails. Free space (`df -h`
for the data dir, `/dev/shm` for arrays), then lower
`HOLDEM_STRATEGY_RETENTION_SECONDS` so old strategies are removed sooner, and
check log rotation (`systemctl --user list-timers holdem-logrotate.timer`).
SQLite needs free space too: with the disk at 0 bytes the dispatcher logs
`database or disk is full` once per tick and queued jobs simply wait (seen in
the Phase 5 soak when a Docker build filled `/`); they resume on their own once
space is freed. A job that was running when the store filled fails with
`disk_full` (the runner retries the write until it succeeds, so it is never
left `solving`). Resubmit any `disk_full` jobs after.
