# Progress log

## 2026-11-16 — Phase 0 environment and viability

- Added `third_party/GPU-CFR` as a read-only submodule at `4524b727642ffc99055d7c51d9eff11744155c27`; did not modify upstream files.
- Host has 2x RTX 5060 Ti (compute capability 12.0), NVIDIA driver `595.71.05`, CUDA toolkit `13.2.78`, Python `3.15.2`, and `uv 0.11.21`.
- Engine venv: `.venv-engine` on Python `3.9.25` with torch `2.8.0+cu128`; `sm_120` is present in `torch.cuda.get_arch_list()` and matmuls pass on both GPUs.
- Engine smoke: `gpu_cfr` imports and the Kuhn `StagewiseCFRSolver` path trains with CUDA graphs active on both `CUDA_VISIBLE_DEVICES=0` and `=1`.
- API venv: `.venv-api` on Python `3.15.2` with torch `2.14.1+cu130` from the cu130 index; fastapi/uvicorn/pydantic/numpy/pytest installed without resolver or wheel failures; both GPUs pass matmul.
- Benchmarks: ran `iter_bench` for `kuhn_poker`, `hunl_river`, and `hunl_turn`, CUDA graph vs eager, on each GPU (`--variant cfr_plus --iters 1000 --warmup 50 --reps 3`). Median ms/iter and peak VRAM are recorded in `docs/PHASE0.md`; raw JSONL is under `scripts/phase0/logs/`.
- Verdict: **GO-A**. Phase 1 may proceed.
- Caveats: GPU-CFR has no license file and GitHub reports `license: null` despite a README CC BY-NC-SA 4.0 badge; `gpu_cfr.eval.exploitability` needs a sibling `project_llm_opp_prior` package not vendored here.

## 2026-11-16 — Phase 1 SolverEngine

- Added `holdem_solver.engine.SolverEngine` wrapping `StagewiseCFRSolver` with `build()`, `solve(iterations|time_budget|target_exploitability)`, progress callbacks, `get_strategy*()`, `evaluate()`, `reset()`, and `close()`.
- Eval check: `gpu_cfr.eval.best_response`, `gpu_cfr.eval.strategy_export`, and `gpu_cfr.eval.metrics` import and run without `project_llm_opp_prior`; `gpu_cfr.eval.exploitability` still fails with the known `ImportError`. No `holdem_solver/eval` fallback was needed; exploitability uses `TwoPlayerZeroSumEvaluator`.
- Added `estimate_vram_bytes()` calibrated from Phase 0 RTX 5060 Ti measurements; examples: Kuhn 11.019 MiB, HUNL river 57.469 MiB, HUNL turn 159.116 MiB estimates.
- Kuhn validation: CPU 2000 iterations gave `exploitability=0.001895875`, `profile_value_p0=-0.055537000`; CUDA gave `exploitability≈0.001895845`, `profile_value_p0≈-0.055537015`; known value `-1/18=-0.055555556`.
- Gate: `scripts/phase1/run_engine_tests.sh` passed `13 passed` on both `CUDA_VISIBLE_DEVICES=0` and `=1`; logs are in `scripts/phase1/logs/`.
- Details and assumptions: `docs/PHASE1.md`.

## 2026-11-16 — Phase 2A process split

- Added `holdem_solver/protocol/` (stdlib only, 3.9 syntax), `holdem_solver/engine_worker/` (3.9; JSON-RPC over a Unix socket, `.npy` in `/dev/shm`, one worker per GPU via `CUDA_VISIBLE_DEVICES`), and `holdem_solver/client/` (3.15; `EngineClient` with build/solve/progress/get_strategy/reset/close/cancel plus start/restart/shutdown).
- Added `FakeEngine` so client tests run without a GPU; it does not run CFR.
- `SolverEngine.solve` gained `should_stop` and `SolveResult.cancelled` so a running solve can be cancelled between chunks.
- Decisions: `solve` is an async job polled with `solve_wait`; a dead worker raises `WorkerDiedError` and restarts unbuilt; a `flock` per socket keeps one worker per GPU and lets a restart clear the stale socket and shm files.
- Gate: `scripts/phase2/run_split_gate.sh` — engine suite `36 passed, 1 skipped` on each GPU, API suite `47 passed`, Kuhn round-trip from `.venv-api` on both GPUs (exploitability ≈ `0.0018958`, value ≈ `-0.055537`, peak reserved 8.0 MiB), worker restarted cleanly after `SIGKILL` on both. Details: `docs/PHASE2.md`.

## 2026-11-16 — Phase 2A review fixes

- Devin Review on PR #3: 1 security and 6 bug findings, all fixed with regression tests: runtime/shm directories and the lock file can no longer be planted by another user; `solve_wait` is tied to a job id; a dead worker is restarted once under concurrent calls; `get_strategy` is serialized and cleans up partial exports; `close` cancels solves admitted while it waits; error responses echo the request id.

## 2026-11-16 — Phase 2B spot builder

- `help()` on `gpu_cfr.games.hunl_river` / `hunl_turn` (saved in `scripts/phase2/logs/hunl_api_help.txt`): both builders take board, pot, stack, `bet_fractions`, and explicit `hands0`/`hands1`. They are not fixed presets, so the EFG-compiler route was not needed. Not supported: raises, range weights, per-player or per-street sizes, flop/preflop.
- Added `SpotConfig` (`holdem_solver/protocol/spot.py`), card and range parsing (`protocol/cards.py`), eight named bet-size sets plus `all_in`, blocker and dead-card removal, and `engine_worker/spot_builder.py`. `EngineClient.build` accepts a `SpotConfig`.
- Decisions: range weights go through `set_root_ranges` after the build; the node count is computed exactly from a one-deal tree and checked against `max_nodes` (default 2M) before building; turn dead cards are rejected because the engine still deals them as river cards.
- Known answers (closed-form nuts/air/bluff-catcher spots): river -1.25, 2.5, -0.833 and -5 within 0.02; two-street turn 0.625 within 0.03 at 40,000 iterations.
- Measured on both GPUs (`scripts/phase2/measure_spots.py`): 37 nodes to 4,446,091 nodes; largest peak reserved 1576 MiB against a 1513 MiB estimate; estimate is 4–11% under peak for spots over 500k nodes. Full table: `docs/PHASE2.md`.
- Gate: `scripts/phase2/run_split_gate.sh` — engine `96 passed, 1 skipped` per GPU, API `100 passed`, Kuhn round-trip and kill/restart OK on both GPUs.

## 2026-11-16 — Phase 3 REST API

- Added `holdem_solver/api` (FastAPI on `.venv-api`, Python 3.15.2): `POST/GET/DELETE /v1/solves`, `GET /v1/solves/{id}/strategy`, `/healthz`, `/v1/gpus`; API-key auth via `HOLDEM_API_KEY`, SQLite job store (`api-data/solves.db`), in-flight jobs marked failed on restart, pydantic models, structured JSON logs, OpenAPI docs.
- One job at a time per GPU, least-loaded idle GPU assignment; full scheduler deferred to Phase 4. Strategies are persisted to `<data_dir>/strategy-<id>.npz` at job end.
- Admission gates: `num_nodes > max_nodes` and `estimated_vram_bytes * 1.15 > 14 GiB` reject with 422. New `estimate_spot` RPC computes the node count on a CPU-side probe tree so it stays responsive during a solve.
- Engine code moved from `holdem_solver/engine/` into `holdem_solver/engine_worker/` (per AGENTS.md); worker `progress` now returns `exploitability_history` and `get_strategy` exports a fourth `actions` array with action names per infoset.
- Full-range river convergence measured (`scripts/phase3/record_convergence.py`, log in `scripts/phase3/logs/`): exploitability `0.0416 → 0.00083` over 4000 iterations at ~64 it/s including evals; noisy after ~2k iters. Table in `docs/PHASE3.md`.
- Gate: `scripts/phase3/api_gate.sh` (curl) passed on both GPUs — parallel solves on gpu0/gpu1, strategy fetch, cancel, and all 422 paths. Tests: `.venv-api` 107 passed (incl. `@pytest.mark.gpu` e2e on the 573k-node river spot), `.venv-engine` 95 passed + 1 skipped. `README.md` has run instructions and curl examples.
- Devin Review on PR #5: 4 bugs fixed in `0e12db9` — worker auto-restart after crash, NaN exploitability samples breaking JSON-RPC encoding, the VRAM gate now respects `solver_options.dtype`, and `HOLDEM_GPUS=""` (gpu `None`) is schedulable via a `_NO_IDLE` sentinel.

## 2026-11-16 — Phase 4 scheduling and operations hardening

- Failure audit of the Phase 3 scheduler (`docs/PHASE4.md`) found five failures it could not survive, each now with a regression test in `tests/test_phase4_failures.py`. Worker OOM was a generic "internal error" and the worker was not respawned. An API restart failed every queued job. A hung worker was never killed and kept getting jobs. On disk full, the `.npz` write could leave a truncated file and skip GPU release. A double `DELETE` was a read-then-write that raced the dispatcher's claim and could revive a cancelled job.
- Scheduler: the SQLite queue now orders by `priority DESC, created_at`, and `HOLDEM_MAX_QUEUE_DEPTH` returns 429 + `Retry-After` when full. Per-job `timeout_seconds` cancels a solve; during a build it kills the worker. VRAM admission learns `max(peak/estimate) × 1.05` from finished jobs (static 1.15 until a ≥64 MiB sample exists) against per-GPU capacity minus a 512 MiB reserve. CUDA OOM now gets JSON-RPC `-32004` → `error_code=cuda_oom`, and the worker is SIGKILLed and respawned.
- Watchdog: pings every `HOLDEM_HEARTBEAT_SECONDS` (5). After `HOLDEM_HUNG_WORKER_SECONDS` (120) with no answer it SIGKILLs and respawns the worker, and the job fails `worker_hung`. A worker-generation check fixes a hang found by the chaos test: after the watchdog had restarted a SIGKILLed worker, the runner polled the fresh worker forever.
- Lifecycle: `Idempotency-Key` on POST (replay → same job, different body → 409). Strategy `.npz` files are written atomically, deleted after `HOLDEM_STRATEGY_RETENTION_SECONDS` (7 d), and `/strategy` returns 410 for them. On restart only building/solving jobs fail (`api_restart`, not replayed); queued jobs keep running.
- Measured (`scripts/phase4/gate.py`, logs in `scripts/phase4/logs/`): 10 mixed jobs (2k–573k nodes) on 2 GPUs in 14.5 s. Max concurrent jobs per GPU was 1 over 286 samples. After one SIGKILL chaos event: 9 done and 1 `worker_died` (exit -9). Real OOM with a 15.2 GiB hog: `cuda_oom` in 6.8 s, worker respawned, and the next job was `done`. SIGSTOP with a 10 s threshold: `worker_hung` 12.5 s after the stop, then respawned. The 153k-node spot peaked at 1.56× its estimate (static factor was 1.15).
- NaN/duplicate exploitability in the Phase 3 full-range log came from the wrapper's polling, not the engine evaluator. Over 17 direct evals there were 0 NaNs. `null` means no eval yet, and the duplicates were 2 s polls between ~3.2 s evals. The final float32 `0.0` is the evaluator clamping a NashConv of −1.5e-4, which is within its float32 tolerance; float64 gives 0.000833.

## 2026-11-16 — Phase 5 release

- VRAM admission seeds the factor at 1.6 (`SEED_FACTOR`) until 3 finished jobs of the same dtype with estimate ≥64 MiB exist, then uses `max(peak/estimate) × 1.05`. The reason is that Phase 4 measured 1.56× on the 153k-node spot: the old 1.15 seed under-admitted, and a single sample is too few to trust. The soak ended with 50 samples per dtype: float32 learned 1.17, float64 learned 1.00 (the float64 estimate over-counts).
- `GET /metrics` (prometheus-client, same auth as `/v1`) exports:
  - jobs by status and failed jobs by `error_code`;
  - queue depth;
  - NVML utilization and VRAM per GPU;
  - build and solve latency histograms;
  - iterations/sec;
  - VRAM factor and sample count;
  - worker restarts, taken from `EngineClient.generation` so every restart path is counted.
- External worker mode (`HOLDEM_WORKER_MODE=external`):
  - The API attaches to systemd-run workers. A worker pid change is treated as a restart.
  - On a hang or OOM the API SIGKILLs the worker and waits for the supervisor to start a new one.
  - Attaching happens on the watchdog thread, so a missing worker does not block startup.
  - Spawn mode is still the default.
- Packaging:
  - `deploy/systemd/user/`: installed here via `deploy/install-user-units.sh`, no sudo, linger on.
  - `deploy/systemd/system/`: root variants, logging to the journal.
  - Also: env template, logrotate config and timer, `Dockerfile` + `compose.yaml` (NVIDIA runtime). Compose refuses to start without `HOLDEM_API_KEY`.
  - The Docker image is ~11 GB (torch cu128 for the 3.9 engine).
- Security:
  - API key compared in constant time.
  - POST token bucket: 120/min, burst 30 → 429 with Retry-After.
  - Body limit 64 KiB → 413.
  - Socket and shm directories 0700, sockets 0600, both tested.
  - The strategy endpoint returns solver output only.
- API:
  - `exploitability_iteration` field.
  - Top-level `dtype`.
  - `?view=hand_grid`: 169 classes with combo-averaged frequencies.
  - An OpenAPI example on every endpoint.
- Benchmarks: `docs/BENCHMARKS.md`, fresh runs of 5 spots × float32/float64 × 2 GPUs. For example, the 9.6M-node full-range river spot: 96 s build, 83 it/s float32 and 56 it/s float64, 3.4 GiB peak, 1e-3 reached in 38 s (float32) and 64 s (float64).
- Gate evidence (`scripts/phase5/logs/`):
  - Tests: `run_gate.sh` engine `95 passed, 5 skipped` on each GPU; API `140 passed` (GPU tests included).
  - Docker: `docker_smoke.txt` has the image built and solving on GPU 0 with both Python versions.
  - Parent pid (`parent_pid.txt`): spawned workers carry `--parent-pid` and exit 0.6 s after the API is SIGKILLed.
  - systemd (`systemd_restart.txt`): restarting `holdem-api` keeps the worker pids. SIGKILL of `holdem-worker@0` → systemd restart (NRestarts=1), the API reattaches and solves on both GPUs, and `holdem_worker_restarts_total{gpu="0"} 1`.
- Soak (`soak.jsonl`, `scripts/phase5/soak.py` against the systemd deployment), 3614 s, 188 rounds, 1316 jobs:
  - Outcomes: 940 done, 188 cancelled (intended), 188 `timeout` (intended).
  - 0 stuck, 0 unexpected. Every strategy fetch passed in both views. 0 worker restarts.
  - Jobs split 652 on GPU 0 and 664 on GPU 1; 846 float32, 470 float64.
  - The full-range job was rejected 422 by `max_nodes=2M` all 31 times, so the largest admitted job was 573k nodes.
- Soak bug found and fixed:
  - Trigger: the first soak attempt (`soak_aborted_diskfull.jsonl`) overlapped a Docker build that filled `/` to 0 bytes.
  - Queued jobs waited and resumed on their own.
  - But one running job's progress write failed, and recording its failure also failed, so it stayed `solving` until the next API restart.
  - Fix: `_run` now classifies SQLite "disk is full" as `disk_full` and retries the terminal write until it succeeds. The failure path also releases the worker, which had kept the dead job's solve running and answered the next build with `engine is busy`. Regression test: `test_store_disk_full_mid_solve_does_not_leave_job_stuck`.
- Reboot test passed (`reboot.txt`). The box booted at 19:30:06 and nothing was started by hand.
  - Linger is on. `holdem-api`, `holdem-worker@0` and `holdem-worker@1` were all active at 19:30:14 with NRestarts=0.
  - The API attached to both external workers.
  - 4 jobs (2 spots × float32/float64) finished `done`, 2 on each GPU, with all 169 classes present in the `hand_grid` view.
  - `/metrics` shows no stuck jobs (0 queued/building/solving), 0 restarts, and both workers healthy.
