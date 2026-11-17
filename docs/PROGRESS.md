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

## Phase 6 — raises (2026-11-16)

- `docs/PHASE6.md` spikes the GameSpec format and documents
  `gpu_cfr.efg.spec`/`compiler`/`efg_file`.
- `engine_worker/tree_builder.py`: pure-Python GameSpec builders for river
  and turn with per-player/per-street `bet_sizes`, `raise_cap` (max raises
  per street), `raise_sizes` (`("x", m)` multiples of the bet faced,
  `("p", f)` fractions of the post-call pot), all-in capping, and the
  shipped builders' deal/blocker logic.
- Consistency gate passed: `raise_cap=0` reproduces
  `build_hunl_river_game` / `build_hunl_turn_game` exactly — identical node
  and infoset counts, equal solved value/exploitability — on 3 river + 2
  turn spots.
- Step 2 measurements (`scripts/phase6/logs/raise_measurements.json`):
  reserved VRAM ≈ 266–311 B/node; biggest tree 10.37M nodes / 2.64 GiB
  (14 GiB admits ≈ 53M nodes); spec build ~6.4 s/M nodes and dominates
  solver allocation past ~1M nodes (61 s of 98 s at cap 2 on the full
  river spot) — caching/vectorization proposal recorded in PHASE6.md.
- Step 3 KAT: `test_river_check_raise_known_answer` — solver reproduces a
  fictitious-play check-raise equilibrium exactly (value 0.3269, JJ raises
  1.000, KQo calls raises 0.859, exploitability 2e-4). KK-vs-KQo blocker
  subtlety documented in PHASE6.md.
- `SpotConfig`/API: `raise_cap`, `raise_sizes`, and mapping `bet_sizes`
  (`{"0":..,"1":..}` per player, `{"turn":..,"river":..}` per street on
  turn spots) carried through `to_dict`/`from_dict`; `needs_custom_builder`
  routes to `tree_builder`. `max_nodes` gate and the API VRAM admission
  gate cover raise trees unchanged (322 B/node estimate stays above the
  measured ~266–311).
- Tests: `tests/test_phase6_raises.py` — 8 tests (3 river gates, 2 turn
  gates, check-raise KAT, raise grammar semantics, SpotConfig API).
- `docs/BENCHMARKS.md`: Phase 6 section with node scaling, build times,
  it/s, and VRAM per cap.

## Phase 7 — cross-validation vs TexasSolver (2026-11-17)

- TexasSolver v0.2.0 console build driven via `scripts/phase7/` mapping layer:
  `texas_input.py` (SpotConfig -> input file), `texas_dump.py` (dump JSON ->
  per-node strategy + pure-python EV walk), `grid.py`, `compare.py`,
  `run_treecheck.py`, `run_suite.py`, `plot_freq.py`. Suite: 15 spots, both
  solvers to <=0.5% of pot, ours float64.
- Results: 4 match / 11 explained / 0 unresolved; root EV within the 1%-of-pot
  tolerance on all 15 spots. Review caught 5 harness bugs (halved
  exploitability units + doubled stop target, unapplied range weights,
  blocked-combo weight restoration in dump_ev, many-to-one action matching,
  stale dump reuse); all fixed, suite re-run. No solver bugs; regression
  tests 18/18 pass in both venvs.
- Semantics decoded + documented: raise_limit=4 hard-coded, all-in snap
  threshold, auto-all-in raise at cap boundary, donk flag on call-ended
  streets, dealcard stubs for all 52 cards, dump=average vs expl=current.
- Evidence: docs/PHASE7.md, scripts/phase7/runs/suite.json, treecheck.json,
  runs/plots/*.png. Tests: tests/test_phase7_mapping.py.

## Phase 8 — correctness solidification (2026-11-17)

- `hand_grid` aggregation now reach-weighted: `w(c)·own_reach(c)·opp_mass(c)`
  (blocked/dead-board combos -> 0); `to_grid` in the compare layer mirrors it
  exactly. Regression tests: `tests/test_phase8_aggregation.py` (7 api-venv /
  5 engine-venv pass).
- TS weight syntax verified: `CLASS:fraction`, identical to `parse_range`'s
  `TOKEN:weight`; `texas_input.py` passes weighted ranges verbatim.
- Weighted suite `scripts/phase8/` (8 spots, cap 0/1): 6 match / 2 explained
  / 0 unresolved; |ΔEV| <= 0.26% pot everywhere. w-dry-cap1 explained by D2
  (TS counts raises per player); w-wet-cap0 by demonstrated non-unique
  equilibrium (EV gap 0.0).
- Cross-evaluation: TS dumped strategy evaluated in our evaluator reproduces
  TS's own reported exploitability to ~0.001pp on the 13 identical-tree spots
  of 14 evaluated (8 weighted + 6 Phase-7 cap-0 rivers). w-turn-cap0 = 16.5%
  — explained by D4 (our tree has the river donk line; TS never defends it;
  mapped profile value matches dump_ev exactly). Reverse direction skipped:
  console has no strategy-load/node-lock command.
- EV-erased frequency gap = 0.04–0.15% of pot on every measured spot: all
  observed mixing differences are between equal-EV actions.
- float32 == float64 on 4 Phase-7 spots (identical EV/exploitability).
  Random batch: 10 boards cap 0 — |ΔEV| <= 0.14% pot, xeval exact on all.
- Bug fixed: engine flat-strategy order is compiled-game order
  (`solver.average_strategy()` keys), NOT `spec.infosets` order.
- Evidence: docs/PHASE8.md, scripts/phase8/runs/{suite8.json,
  xeval_phase7.json, float32.json, random_batch.json}. Single command:
  scripts/phase8/run_suite.sh.

## Phase 8.5 — cleanup, small validations, documentation (2026-11-17)

- Tolerances written in `docs/PHASE8_5.md` before any run; unchanged since.
- Doc corrections: PHASE8 status → complete, cross-eval count 13/14,
  EV-erased gap 0.04–0.15% of pot, "identical" float32 claim corrected
  (measured diffs 1e-7–3e-6 chips, `scripts/phase8_5/runs/float32_rerun.json`).
- Donk verified configurable on the TexasSolver console:
  `set_bet_sizes oop,river,donk,50` produced `BET 25.0` on the OOP river
  node after a called turn bet (`scripts/phase8_5/runs/donk_probe_*`).
  PHASE7 D4 updated; PHASE8's "cannot be configured" claim removed.
- `texas_input.py`: `emit_donk` emits `oop,<street>,donk,<sizes>`; the
  re-run turn suite is 2 match / 1 explained — w-turn-cap0's Phase-8 16.5%
  cross-eval gap closed to 0.069% on an identical tree. turn-narrow-a stays
  explained via D2-ext (TS per-player raise counting).
- dtype plumbing verified end-to-end: `describe()` and `rpc_ping` now echo
  the requested dtype; `regret_sum` + flat strategy honor it;
  `strategy_sum` accumulates in float64 (measured, asserted). Two identical
  float32 solves are bitwise-identical (deterministic).
- 5 per-player/per-street size spots + 4 raise-combination spots: all match
  or explained; `("x",m)` raises are expressible in TS per bet size via
  `pct = 100·(m−1)·bet/(pot+2·bet)` (`x_raise_pcts` mapping option).
- API parity fix: `SolveRequest` accepts `bet_sizes` dicts and
  `raise_cap`/`raise_sizes` (raises were unreachable over REST).
- Tests: `scripts/run_all_tests.sh` runs both suites + collection coverage;
  new `tests/test_texas_input.py` and dtype regression tests.
- Evidence: `docs/PHASE8_5.md`, `scripts/phase8_5/runs/suite8_5.json`,
  `float32_rerun.json`. Docs added: VALIDATION.md, ARCHITECTURE.md,
  INDEX.md, CHANGELOG.md; README game-rules/API sections rewritten.

## Phase 8.6 — raise-cap equivalence, full turn validation (2026-11-17)

- TexasSolver `raise_limit` resolved from v0.2.0 source
  (`src/GameTree.cpp::buildAction`): one shared `raise_times` counter per
  street; the opening bet counts (sets it to 1), each raise increments it
  regardless of player; `raise_limit=4` hard-coded → bet + 3 raises max.
  Earlier "counts per player" wording corrected (harmless — raises
  alternate players anyway). Equal trees iff `raise_cap` ∈ {0, 3} or lower
  caps where deeper raises are physically impossible.
- `scripts/phase8_6/` — 4 equal-tree spots, all `tree.ok` with 0
  mismatches, all cross-evals under 1.0% of pot:
  `turn-narrow-a-cap3` 0.041% (the D2-ext re-raise line `b5→r17→r30` now
  exists on both sides — the 15.37% gap was the missing line, not a solver
  difference); `turn-narrow-a-allin` 0.347% (cap 1, all-in raise);
  `river-cap2-eq` 0.000% (cap 2, second raise already all-in);
  `river-cap3-eq` 0.016% (full TS depth `b10→r34→r86.8→r100`).
  "explained" verdicts are equal-EV mixing only; EV gaps ≤ 0.003% pot.
- VALIDATION.md: "Not validated" section added (flop, multiway, reverse
  xeval impossible — no strategy-load command, cap ≥4, per-phase skips);
  turn-narrow-a moved from explained to resolved.
- REST test `test_raises_and_per_player_bet_sizes_over_rest`
  (tests/test_api_gpu.py): POSTs raise_cap/raise_sizes/per-player
  bet_sizes, verifies num_nodes = 1 + 108×15 and every node's action set
  against SpotConfig semantics via /strategy.
- Evidence: `docs/PHASE8_6.md`, `scripts/phase8_6/runs/suite8_6.json`.

## Phase 9 — UI backend (2026-11-17)

- New endpoints: `GET /v1/solves/{id}/tree` (node navigation with
  children summary + chance-node dealt cards, no client history
  parsing), `GET /v1/solves/{id}/node` (per hand class / per combo
  frequencies, per-action EVs, hand EV, equity, ranges),
  `GET /v1/solves/{id}/events` (SSE, `Last-Event-ID` replay),
  `POST /v1/solves/{id}/pin|unpin`, `/v1/spots` CRUD + `duplicate` +
  `{id}/solve` with limits (512 spots / 200-char names / 2000-char
  notes / 32×64-char tags), `POST /v1/auth/login|logout` (HttpOnly
  SameSite=strict session cookie + CSRF; CORS off unless
  `HOLDEM_CORS_ORIGINS`; login rate-limited 10/min burst 5).
- `holdem_solver/protocol/evwalk.py`: pure-Python evaluation walk
  mirroring the Phase 7 `dump_ev` reference — shared by both venvs
  (3.9-safe). EV gate: 6/6 cap-0 river spots, 1,756 (node, combo,
  action) EV values vs `xeval.node_action_evs`, **max |Δ| =
  7.993605777301127e-15** (float64 round-off); root EV =
  `dump_ev` = `profile_value_p0` to ~1e-14
  (`scripts/phase9/runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`;
  Phase 9.1 regenerated — the originally committed file was a stale
  4-spot partial run).
- Subtree cache (step 6): mechanism shipped since Phase 6; re-verified
  — `compare_trees` green and node counts identical on all 10 rerun
  spots; spec-build on `river-full-pot` cap 2 = **68.01 s /
  10,374,211 nodes** (Phase 6: 60.7 s — +12% machine variance, no code
  change).
- Curl evidence on both GPUs in `scripts/phase9/logs/` (job
  `b905de9c0501` on gpu 0, `e82d19066e0e` on gpu 1); every number in
  docs copied from those logs. Gate `run_all_tests.sh` green.
- Evidence: `docs/PHASE9.md`, `scripts/phase9/`.

## Phase 9.2 — evidence-guard hardening (2026-11-17)

- `scripts/phase9/check_evidence.py` rewritten: parses numeric claims
  straight out of `docs/*.md` + `README.md` — totals, spot counts,
  `max |Δ|`, slash-separated per-spot lists mapped to the manifest spot
  order (per-spot values AND sum checked; spot names must appear in
  manifest order), and bare `N/M` fractions cited beside
  `runs/brute_ev.json`. Claims >6 lines from a citation fail.
- `tests/test_doc_evidence.py`: positive test plus five parametrised
  mutations of a temp `PHASE9.md` copy (per-spot count, total, spot
  count, `max |Δ|` digit, spot order) — all fail as required.
- Count-change cause (step 3): **1,657 was a transcription error, not a
  code/data change.** Both recorded runs and the manifest in
  `scripts/phase9/runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`
  show 84 / 273 / 392 / 266 / 229 / 512 = 1,756; the number was written
  at `c3bcfb8` from a stale 4-spot partial artifact (1,399). The only
  EV-path commit between the two doc states, `1302fc8`
  (advance past `river:` elements + scientific-notation bets in
  `evwalk.py`), is inert on river spots — no `river:<card>` elements,
  no sci-notation bet names, no nodes added. `35df7c9` re-measured.
- brute_ev (step 4): **no uncompared emitted rows** — the `18/21` on
  w-dry-cap0 was compared-at-root / compared-at-check. TexasSolver emits
  strategy rows only for dealable combos (verified on the four committed
  `scripts/phase8_6/runs/*_ts.json` dumps): range0 loses 6 combos to
  board-blocking (3 `Ks`-AKo + 3 `2d`-22) → 18/18 compared; check-node
  range1 loses 3 (`Ks`-AKo) → 21/21 compared. `brute_ev.py` now writes
  per-node `rows` and `skipped` reasons; PHASE9_1.md wording fixed.
- Sessions (step 5): README documents stateless HMAC sessions and that
  logout does not revoke a stolen cookie; lifetime configurable via
  `HOLDEM_SESSION_TTL_SECONDS` (`HOLDEM_SESSION_SECONDS` fallback),
  tested in `test_session_ttl_seconds_env`.
- Gate: `run_all_tests.sh` — engine 144 passed/14 skipped; api
  203 passed/9 skipped/3 failed — all 3 pre-existing/environmental
  (GPU VRAM assertion, an SSE race failing identically on `main`, one
  timing flake that passed on rerun).
- Evidence: `docs/PHASE9_2.md`, `scripts/phase9/check_evidence.py`.
## Phase 10 Part 0 — green gate, SSE race fix, evidence guards (2026-11-17)

- SSE race root cause (fixed in the server): `store.cancel()` on a
  running job sets `cancel_requested=1` but leaves `status` active until
  the runner's `finish()` lands — an SSE client connecting in that window
  saw a stale `solving`/`building` snapshot after DELETE had reported
  `cancelled`. `_sse_snapshot` now reports the effective status; both
  terminal-state checks compare it. `test_sse_finished_and_cancelled_jobs`
  **50/50 consecutive runs green**; `test_sse_evicted_id_gets_snapshot_
  no_replay` rewritten deterministic (store-driven job, lockstep
  `store.update()` reads, no timing sleeps); whole SSE file
  **20/20 consecutive runs green** (~44 s each, 11 tests).
- `scripts/phase10/check_suites.py`: verifies the Phase 7/8/8.5/8.6
  result tables and the VALIDATION verdict-counts table against the
  committed suite JSONs — EVs, ΔEV chips/%pot, mean/max freq diffs,
  exploitability %, verdicts, cross-eval percentages, mapped-infoset
  `N/M`, EV-gap bounds and ranges — at half-ulp of the doc's printed
  precision. Missing tables, malformed `N/M` cells and verdict-count
  drift all fail. Review round hardened it further: `req_table`, fullmatch
  on `N/M`, proportional float slack.
- Doc corrections the guard forced (printed values stale vs committed
  artifacts): PHASE7 explained count 10→**11**; PHASE8 w-pair-cap0
  max|df| 0.27→**0.26**; PHASE8_5 turn-narrow-b ΔEV 0.069→**0.034** and
  w-turn-cap0 0.035→**0.018**; VALIDATION Phase-8 row 6/2/0→**5/3/0**.
- Doc-status check: `Status: in progress` + a recorded gate result fails;
  PHASE9_2.md corrected to `complete` (its no-GPU gate relabeled
  superseded).
- Artifact gap recorded, not fixed: per-spot `*_ts.json` TS dumps for
  phases 7/8/8.5 stay gitignored; the guard checks only committed suite
  JSONs.
- Gate: `run_all_tests.sh` on gpu-5060ti — engine **160 passed /
  7 skipped** (all deliberate per-venv exclusions), api **225 passed /
  0 skipped**, collection coverage PASS. Guard: 19 docs OK;
  `test_doc_evidence.py` **20 passed** (incl. 10 parametrized mutations +
  doc-status).
- Evidence: `docs/PHASE10.md`, `scripts/phase10/logs/` (real-GPU SSE curl
  transcripts: running solve, valid/stale reconnect, finished job).

## 2026-11-17 — Phase 10 Part 1: web UI at /ui

- `ui/`: React 19 + TS + Vite 7 app, OpenAPI-generated client
  (`openapi-typescript` + `openapi-fetch`) with TanStack Query; served
  from `ui/dist` at `/ui` with SPA fallback and a `/` redirect.
- API additions: `GET /v1/solves` (newest first), `POST /v1/estimate`,
  `pinned` + `spot_name` on `SolveStatus`, `_SPAFiles` mount.
- Screens: login, spot setup (board/dead pickers, dual 13x13 range
  editor with weights + import/export, bet-size modes, raises, dtype,
  stop conditions, inline 422 + estimate), jobs list with pin/cancel,
  SSE exploitability chart, strategy viewer (tree nav + breadcrumbs +
  action-mix grid + per-class/combo detail + reaches + dealt cards),
  spot library, compare view.
- Playwright (`ui/tests/e2e.spec.ts` vs fake engine): **6 passed**
  (15.9 s) — login, river spot build + estimate + solve + root + child,
  pin, save/rename/tag/duplicate/solve-from-library, SSE reconnect,
  inline 422. Log: `scripts/phase10/logs/playwright_e2e.log`.
- Real e2e on the outpost (`ui/scripts/real-e2e.mjs` vs systemd API):
  two river solves, gpu0 `1da488305561` expl 0.0931 / gpu1
  `08a0453b5a93` expl 0.2259 at 120 iterations; screenshots 01–07 in
  `scripts/phase10/logs/`, job snapshot in `real_e2e.json`.
- Guard: `check_phase10` verifies the doc table vs `real_e2e.json`,
  the suite count vs `playwright_e2e.log`, and the committed
  screenshots. `check_suites.py`: 19 docs OK.
