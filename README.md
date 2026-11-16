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

Spots over `max_nodes` (default 2M) or whose 1.15x VRAM estimate exceeds 14
GiB are rejected with 422. One job runs per GPU; queued jobs go to the
least-loaded GPU. See `docs/PHASE3.md`.
