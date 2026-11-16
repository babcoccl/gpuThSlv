# gpuThSlv

GPU-accelerated heads-up no-limit Texas Hold'em CFR solver, exposed as a REST
API. Two runtimes: a Python 3.15 API process (`.venv-api`) and one Python 3.9
engine worker per GPU (`.venv-engine`), talking JSON-RPC over Unix sockets.
See `AGENTS.md` and `docs/PROGRESS.md`.

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

Spots over `max_nodes` (default 2M), or whose predicted VRAM peak (the
estimate × a factor learned from measured peaks, 1.15 until learned) exceeds
14 GiB or every GPU, get 422. One job runs per GPU at a time.

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
