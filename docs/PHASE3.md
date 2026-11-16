# Phase 3 — REST API

Date: 2026-11-16. Branch: `devin/phase3-rest-api`.

## Layout

| Package | Runtime | Notes |
| --- | --- | --- |
| `holdem_solver/api/` | `.venv-api` (3.15) | FastAPI app, pydantic models, SQLite job store, scheduler. Imports only `client` + `protocol` — never torch/`gpu_cfr`/`engine_worker`. |
| `holdem_solver/engine_worker/{solver,memory}.py` | `.venv-engine` (3.9) | Formerly `holdem_solver/engine/`; moved here per the Phase 3 spec so every `gpu_cfr`/torch import lives under `engine_worker/` (AGENTS.md rule, unchanged). |

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/healthz` | Liveness; unauthenticated. |
| GET | `/v1/gpus` | Worker `ping` state + pynvml memory/utilization per configured GPU. |
| POST | `/v1/solves` | `SpotConfig` fields + `variant`, `iterations` \| `time_budget` \| `target_exploitability`, `max_iterations` (cap for target mode), `progress_every`, `solver_options`. 202 `{id}`. |
| GET | `/v1/solves/{id}` | status `queued`/`building`/`solving`/`done`/`cancelled`/`failed`, iteration, `exploitability` + `exploitability_history`, elapsed, gpu, node/infoset counts, estimated + measured VRAM, error. |
| GET | `/v1/solves/{id}/strategy` | Per-infoset action frequencies (see "Strategy mapping"). 409 while the job is active; 404 if nothing was exported. |
| DELETE | `/v1/solves/{id}` | Cancel. Queued jobs are cancelled instantly; running jobs get a cancel the worker honors at the next chunk. |

- Auth: `HOLDEM_API_KEY` env var; send `Authorization: Bearer <key>` or `X-API-Key`. Unset means no auth (dev). All `/v1/*` routes require it.
- OpenAPI docs at `/docs`; structured JSON logs on stderr.
- Job store: SQLite at `$HOLDEM_API_DATA_DIR/solves.db`. On startup every `queued`/`building`/`solving` row is marked `failed` ("api restarted before the job finished").
- Strategies are exported to `$HOLDEM_API_DATA_DIR/strategy-<id>.npz` when a job ends (done or cancelled) and the worker's solver is released for the next job.
- Scheduling: one job at a time per GPU; a queued job goes to the idle GPU with the fewest finished jobs. The real scheduler is Phase 4.
- Config env vars: `HOLDEM_GPUS` (default `0,1`), `HOLDEM_SOLVER_ENGINE` (`real`|`fake`), `HOLDEM_API_DATA_DIR`, `HOLDEM_API_MAX_VRAM_BYTES` (default 14 GiB), `HOLDEM_API_PORT`/`HOLDEM_API_HOST`, `HOLDEM_API_LOG_LEVEL`.

## 422 gates at POST

- At least one stop condition; `iterations` and `time_budget` are exclusive; `target_exploitability` also requires `time_budget` or `max_iterations` (target mode has no cap in the engine — the API enforces the cap by cancelling).
- Exact node count is computed on the worker by a new `estimate_spot` RPC (one-deal probe tree; runs off the engine executor so it stays responsive during a solve). `num_nodes > max_nodes` → 422. `estimated_vram_bytes * 1.15 > 14 GiB` → 422.

## Strategy mapping

`GET /v1/solves/{id}/strategy` returns `{id, iteration, num_infosets, unmapped_keys, infosets[]}`; each infoset is `{key, player, hand, history, actions[{name,label,frequency}]}`.

- Key grammar (from the engine's HUNL builders, confirmed by dumping `spec.infosets`): `p<player>|<hand>|<history>` where `<history>` is pipe-separated street sections of comma-separated action names. River spots have one history section; turn spots have `<turn actions>|<river card>|<river actions>` — the bare card field is reported inside `history` as `"river:<card>"`.
- Action names come from the worker (`get_strategy` now also exports an `actions` array aligned with `keys`): `check`, `fold`, `call`, and `b<chips>` (labelled `"bet <chips>"`).
- Gaps: keys that don't match `p<player>|...` — Kuhn games and `FakeEngine`'s `fake/NNNNN` — are returned with `player`/`hand`/`history` null and counted in `unmapped_keys`. Weights/blocked deals are not represented in keys, so a strategy row can't be traced back to an individual weighted combo beyond the hand name.

## Convergence: full-range river

`scripts/phase3/record_convergence.py` on GPU 0, `{"board":"KsJsTh7d2c","range0":"*","range1":"*","pot":20,"stack":100,"bet_sizes":"pot"}` — the whole `*`x`*` range is 9,631,711 nodes / 4,324 infosets, build 96.4 s, est. VRAM 3.19 GiB, measured peak reserved 3.55 GiB. Raw log: `scripts/phase3/logs/river_full_convergence.jsonl` (a few lines have `exploitability: null` — the engine reports NaN/duplicate evals mid-run; the table uses the real values).

| Iterations | Elapsed (solve) s | Exploitability |
| --- | --- | --- |
| 500 | 19.1 | 0.0416 |
| 750 | 21.1 | 0.0174 |
| 1000 | 25.1 | 0.0143 |
| 1250 | 27.1 | 0.00922 |
| 1500 | 31.1 | 0.00587 |
| 1750 | 33.1 | 0.00472 |
| 2000 | 37.1 | 0.00255 |
| 2250 | 39.1 | 0.00153 |
| 2500 | 43.1 | 0.00162 |
| 2750 | 45.1 | 0.00204 |
| 3000 | 49.1 | 0.00130 |
| 3250 | 51.1 | 0.00140 |
| 3500 | 55.1 | 0.00138 |
| 3750 | 57.1 | 0.00157 |
| 4000 | 61.1 | 0.00083 |

Roughly 64 iterations/s including one best-response eval every 250 (each eval ~2 s; the CFR steps themselves run at ~125/s). Exploitability falls about 50x over 4000 iterations but is noisy after ~2k — occasional upticks (2250→2750, 3250→3750) before the final 0.00083. The first measured convergence curve on a real-sized spot; 4k CFR+ iterations is a reasonable default budget for full-range river spots at ~1e-3 exploitability.

## Tests

- `tests/test_api.py` — FakeEngine end-to-end: healthz/auth, `/v1/gpus`, solve lifecycle + strategy, every 422 case, queued + running cancels, 404s, restart re-open.
- `tests/test_api_gpu.py` (`@pytest.mark.gpu`) — real engine: the 573,031-node broad river spot through HTTP, asserts `exploitability_history` decreases and measured VRAM > 0, strategy fully mapped.
- Gate: `scripts/phase3/api_gate.sh` — uvicorn up, curl on both GPUs (two solves, status, strategy, cancel, 422s).

## Design decisions

1. **API caps for target mode run in the job runner.** The engine accepts exactly one stop mode, so `time_budget`/`max_iterations` are enforced by polling `progress()` once a second and cancelling — the same path DELETE uses.
2. **`estimate_spot` is a separate RPC, not part of `build`.** It must answer at POST time even while the GPU is busy solving; it builds only a one-deal probe tree (CPU, no solver state).
3. **The engine code moved into `engine_worker/`**, not AGENTS.md amended — the import direction the spec wanted is now literal: `holdem_solver.engine` no longer exists.
4. **Strategy is persisted, not served live.** After a job ends its GPU is released for the next job; keeping strategies in `.npz` avoids a race where the next build wipes the strategy a client meant to fetch.
