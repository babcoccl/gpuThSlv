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
- Decisions: range weights go through `set_root_ranges` after the build; the node count is computed exactly from a one-deal tree and checked against `max_nodes` (default 5M) before building; turn dead cards are rejected because the engine still deals them as river cards.
- Known answers (closed-form nuts/air/bluff-catcher spots): river -1.25, 2.5, -0.833 and -5 within 0.02; two-street turn 0.625 within 0.03 at 40,000 iterations.
- Measured on both GPUs (`scripts/phase2/measure_spots.py`): 37 nodes to 4,446,091 nodes; largest peak reserved 1576 MiB against a 1513 MiB estimate; estimate is 4–11% under peak for spots over 500k nodes. Full table: `docs/PHASE2.md`.
- Gate: `scripts/phase2/run_split_gate.sh` — engine `96 passed, 1 skipped` per GPU, API `100 passed`, Kuhn round-trip and kill/restart OK on both GPUs.
