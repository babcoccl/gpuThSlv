# Phase 4 — Hardening scheduling and operations

Date: 2026-11-16. Branch: `devin/phase4-hardening`.

## Failures the Phase 3 scheduler could not survive

Read from `main` at `343ac02` (`holdem_solver/api/scheduler.py`, `store.py`,
`app.py`). Each row has a regression test in `tests/test_phase4_failures.py`
(fake engine, no GPU) and, where it needs real CUDA, a measured run of
`scripts/phase4/gate.py`.

| Failure | Phase 3 behaviour | Phase 4 behaviour | Test |
|---|---|---|---|
| Worker CUDA OOM mid-build (or mid-solve) | `build` raised a generic `RemoteEngineError`; `_run` caught it and stored `"internal error running the job; see api logs"`. The worker was **not** respawned (auto-restart only fires on worker death), so the next job reused a process that had just hit a CUDA error. | Worker maps OOM to JSON-RPC `-32004` → `EngineOOMError`. Job fails with `error_code=cuda_oom` and a message naming phase, GPU and predicted peak. The worker is SIGKILLed and respawned before the GPU takes another job. | `test_cuda_oom_fails_job_clearly_and_respawns_worker[build,solve]`; gate `--mode oom` |
| API crash mid-solve | `fail_in_flight` on start failed **every** `queued`, `building` and `solving` job, so the queue was lost. | `recover_after_restart()` fails only `building`/`solving` jobs (`error_code=api_restart`, reason in `error`) and does not replay them. `queued` jobs keep their priority and run. Orphan workers exit via the existing parent watchdog. | `test_api_crash_mid_solve_fails_running_job_keeps_queue` (SIGKILLs a real uvicorn process) |
| Hung worker | No heartbeat. A stopped worker blocked the runner until the RPC timeout (30 s for `progress`, 1800 s for `build`), and then the job failed as "internal error". The hung process stayed alive and the next job was sent to it. | A watchdog thread pings each worker every `heartbeat_seconds`. After `hung_worker_seconds` with no answer it SIGKILLs the worker (SIGTERM is not enough for a stopped process), fails the job with `worker_hung`, and respawns the worker. | `test_hung_worker_is_killed_respawned_and_job_failed` (SIGSTOP); gate `--mode hung` |
| Disk full when saving a strategy | `np.savez` straight to the final path; `OSError` escaped to "internal error", `_release` was skipped, and a truncated `.npz` could be left that `/strategy` would later try to read. | Writes `strategy-<id>.npz.tmp`, fsyncs, then `os.replace`. On failure the temp file is removed and the job fails with `disk_full` (`ENOSPC`/`EDQUOT`) or `strategy_save_failed`. The GPU is released first. | `test_disk_full_on_strategy_save` (writes to `/dev/full`) |
| Two clients cancelling the same job | `DELETE` did `request_cancel` and then a separate `update(status='cancelled')` if it had read `queued`. That raced with the dispatcher's `claim`: a job could be marked `cancelled` while it was building, and the runner's later unconditional `update` would revive it to `done`. | `store.cancel()` is one transaction: queued → `cancelled` (terminal), active → `cancel_requested=1`. All runner writes are guarded (`transition`/`finish` only from a non-terminal state), so a terminal job can't be revived. Both clients get 200 with the same final state. | `test_two_clients_cancel_same_running_job`, `..._queued_job`, `test_cancel_vs_claim_race_has_one_winner` |

Also added and tested: priority order (`test_priority_order`), queue-depth 429
(`test_queue_depth_429_and_idempotent_replay_when_full`), idempotency keys
(`test_idempotency_keys`), learned VRAM admission
(`test_vram_aware_dispatch_uses_measured_peaks`), build and solve timeouts
(`test_job_timeout_*`), and retention (`test_strategy_retention_cleanup`).

## Design

**Persistent queue.** SQLite stays the queue (`api-data/solves.db`, WAL). New
columns are added by an idempotent migration: `priority`, `timeout_seconds`,
`idempotency_key` + `request_hash`, `error_code`, `predicted_vram_bytes`,
`peak_vram_bytes`, `dtype`, `strategy_expired_at`. Dispatch order is
`priority DESC, created_at, rowid`. The head job is offered to the first idle
healthy GPU whose capacity fits it. A job that fits no GPU stays queued, and
lower-priority jobs do not jump it (strict priority, no starvation of big jobs).

**Admission (POST /v1/solves).**
1. `Idempotency-Key` header: same key + same body → the original job with
   `replayed: true` (202 again, even when the queue is full). Same key +
   different body → 409. A unique partial index makes concurrent same-key POSTs
   produce one job.
2. Queue depth: `HOLDEM_MAX_QUEUE_DEPTH` (default 64) queued jobs. The count
   and the insert share one transaction. When full → **429** with
   `Retry-After: 30`.
3. VRAM: predicted peak = `estimate × factor`. `factor` is learned
   (`holdem_solver/api/vram.py`): it is the max measured
   `peak_reserved / estimate` over the last 50 finished jobs of that dtype with
   estimates ≥ 64 MiB, ×1.05. Until such a sample exists the factor is the
   Phase 3 static 1.15. The prediction is rejected with 422 if it exceeds
   `HOLDEM_API_MAX_VRAM_BYTES` or every GPU's capacity (NVML total minus
   `HOLDEM_VRAM_RESERVE_BYTES`, default 512 MiB). The 422 detail names the
   factor and where it came from.

**Per-job timeout.** `timeout_seconds` in the body (or
`HOLDEM_JOB_TIMEOUT_SECONDS`) counts from claim. A build can't be interrupted,
so a timeout during the build kills and respawns the worker. During the solve
the scheduler sends `cancel`, keeps the partial strategy, and marks the job
`failed`/`timeout`. If the worker doesn't stop within
`HOLDEM_CANCEL_GRACE_SECONDS` (30), it is killed.

**Watchdog.** One thread per GPU calls `ping(timeout=heartbeat)` every
`HOLDEM_HEARTBEAT_SECONDS` (5). If there has been no answer for
`HOLDEM_HUNG_WORKER_SECONDS` (default 120; a full-range build was measured
making pings take up to 14.8 s), it SIGKILLs and respawns the worker, and the
runner fails the job with `worker_hung`. While a worker is being replaced the
GPU is `healthy: false` in `/v1/gpus` and gets no new jobs.

**Worker death.** A `generation` counter on `EngineClient` is checked on every
poll. This fixed a bug found by the chaos test: when the watchdog's ping saw
the death first and auto-restarted the worker, the runner's next `progress()`
reached the fresh, idle worker and polled it forever. Jobs now fail with
`worker_died` and the exit code (e.g. `-9`).

**Lifecycle.** Strategy files are deleted `HOLDEM_STRATEGY_RETENTION_SECONDS`
(7 days) after the job finishes. A sweep runs every 300 s and also removes
orphan `.npz`/`.npz.tmp` files with no job row. Job rows are kept, with
`strategy_expired_at` set, and `/strategy` returns **410** for them. Status
(`GET /v1/solves/{id}`) survives API restarts.

New `SolveStatus` fields: `error_code` (`cuda_oom`, `worker_hung`,
`worker_died`, `timeout`, `disk_full`, `strategy_save_failed`, `api_restart`,
`engine_error`, `internal`), `priority`, `timeout_seconds`,
`predicted_vram_bytes`, `peak_vram_bytes`, `strategy_available`,
`strategy_expired_at`.

## Measured results (real engine, 2× RTX 5060 Ti)

### 10 mixed jobs on two GPUs + SIGKILL chaos

`.venv-api/bin/python scripts/phase4/gate.py --mode concurrency`
→ `scripts/phase4/logs/gate_concurrency.jsonl`. River `KsJsTh7d2c`, four sizes,
priorities 0–2, all ten submitted at once. The worker running job 5 (573k
nodes) was SIGKILLed 0.5 s after it entered `solving`.

| # | nodes | prio | gpu | status | error_code | build s | total s | estimate MiB | predicted MiB | peak MiB |
|---|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| 0 | 573,031 | 0 | 0 | done | | 5.81 | 7.86 | 204.7 | 235.2 | 228.0 |
| 1 | 1,999 | 1 | 1 | done | | 0.42 | 1.44 | 11.7 | 13.4 | 8.0 |
| 2 | 17,731 | 2 | 1 | done | | 0.13 | 1.15 | 17.0 | 19.5 | 12.0 |
| 3 | 152,851 | 0 | 0 | done | | 1.38 | 2.41 | 62.7 | 72.0 | 98.0 |
| 4 | 1,999 | 1 | 0 | done | | 0.03 | 1.05 | 11.7 | 13.4 | 8.0 |
| 5 | 573,031 | 2 | 1 | **failed** | worker_died | | | 204.7 | 235.2 | |
| 6 | 17,731 | 0 | 0 | done | | 0.11 | 1.13 | 17.0 | 19.5 | 12.0 |
| 7 | 152,851 | 1 | 1 | done | | 1.81 | 2.84 | 62.7 | 72.0 | 98.0 |
| 8 | 1,999 | 2 | 0 | done | | 0.05 | 1.07 | 11.7 | 13.4 | 8.0 |
| 9 | 17,731 | 0 | 1 | done | | 0.11 | 1.13 | 17.0 | 19.5 | 12.0 |

- Wall time 14.5 s. 5 jobs per GPU. A 50 ms sampler took 286 snapshots of the
  job table: the max number of `building`/`solving` jobs per GPU was **1** on
  both GPUs. The scheduler's per-GPU `(start, end)` intervals don't overlap.
- Chaos: worker pid 87735 on gpu 1 SIGKILLed at t=8.58 s. Job 5 failed with
  `worker_died` ("… worker exited with code -9; a fresh worker was started …").
  Gpu 1 was respawned and ran its remaining jobs (7, 9). 9/10 done, 1 failed
  cleanly, 0 stuck.
- **Measured peaks beat the static factor.** The 153k-node spot peaked at
  98.0 MiB against a 62.7 MiB estimate (ratio **1.56**), well above the static
  ×1.15 that Phase 3 admitted with. The 573k spot was 1.11, and the small spots
  were ≤0.71 (the allocator's 2 MiB blocks dominate). This is why admission now
  learns from measured peaks. The 64 MiB sample floor keeps tiny spots from
  pulling the factor down, so here the learned factor would be 1.56 × 1.05 =
  1.64.

The same scenario runs as `tests/test_phase4_gpu.py` (`@pytest.mark.gpu`), and
with the fake engine as `tests/test_phase4_concurrency.py`. The fake version
also injects one OOM, one engine error and one timeout into the ten jobs, and
checks each gets its own `error_code`.

### Real CUDA OOM

`gate.py --mode oom --gpu 1` (`logs/gate_oom.jsonl`): a second process held
15.2 GiB on gpu 1 (116 MiB free), then a 573k-node river job was submitted.
The job failed after 6.8 s with `cuda_oom`: "CUDA out of memory while building
on gpu 1 (predicted peak 0.23 GiB of 15.93 GiB): AcceleratorError: CUDA error:
out of memory. The worker was respawned; …". Worker pid 88250 → 88347. After
the hog was freed, the same spot on the respawned worker finished `done` (peak
228 MiB).

### Hung worker

`gate.py --mode hung --gpu 0 --hung-seconds 10` (`logs/gate_hung.jsonl`): the
worker was SIGSTOPped mid-solve at iteration ~4200 of the 573k spot. The
watchdog logged "missed its heartbeat for 10.1s" and killed it. The job failed
`worker_hung` **12.5 s** after SIGSTOP (10 s threshold + 1 s heartbeat + 1 s
runner poll). The new worker (pid 88133 → 88456) solved the next job `done`.

## Gate

`scripts/phase4/run_gate.sh` (logs in `scripts/phase4/logs/pytest_*.txt`):

- `.venv-engine`, `CUDA_VISIBLE_DEVICES=0` and `=1`: `95 passed, 4 skipped`
  each (the three Phase 4 API test modules skip because fastapi is missing,
  plus the existing 3.10+ protocol skip).
- `.venv-api`, `-m "gpu or not gpu"`: `124 passed`. That includes
  `tests/test_phase4_failures.py` (15) and `test_phase4_concurrency.py` (1).
  The GPU test `test_phase4_gpu.py` passed 6/6 in a row on its own after the
  exit-code race fix.

The script waits for the engine run's workers to exit before the API suite
starts. Without that wait, one run hit `another engine worker already owns
.../gpu0.sock` in `test_api.py`, because a fake worker from the engine suite
was still releasing the socket lock.

## NaN / duplicate exploitability in the Phase 3 river log

Probe: `scripts/phase4/probe_exploitability.py` on the same full-range river spot
(`KsJsTh7d2c`, `*` vs `*`, pot bets; 9,631,711 nodes). `--mode direct` calls the
engine's `TwoPlayerZeroSumEvaluator` in-process every 250 iterations;
`--mode worker` repeats the Phase 3 path and also logs `exploitability_iteration`
and the worker's per-eval `exploitability_history`. Logs:
`scripts/phase4/logs/nan_probe_{direct,worker}.jsonl`.

Verdict: **the NaN and duplicates come from our wrapper's logging, not the engine.**
The one real engine quirk is the final `0.0`.

1. **No NaN anywhere.** 17 direct evals (iterations 0–4000): every
   `BestResponseResult` field was finite, and `average_strategy_flat()` had 0
   NaN slots every time. The `null`s in `river_full_convergence.jsonl` are
   `exploitability: None`, meaning "no eval yet". The worker's `progress()`
   returns `None` until the first `progress_every` eval finishes. The first
   eval is late because `SolverEngine` builds the evaluator lazily on first
   use (3.49 s for this tree), so the first three 2 s polls showed
   iteration 3/250 with `null`.
2. **The duplicates are poll snapshots, not repeated evals.**
   `record_convergence.py` printed `client.progress()` from the
   `solve(on_progress=...)` callback, which fires every 2 s
   (`_WAIT_POLL_SECONDS`) whether or not a new eval has landed. One eval
   happens per ~3.2 s (250 iterations at ~78 it/s), so roughly every other
   poll repeated the last value. The worker log shows the same
   `exploitability_iteration` on both lines of each duplicated pair, and
   `exploitability_history` has exactly 16 evals with no repeats.
3. **The Phase 3 table was off by one eval.** Each poll line paired the *live*
   `iteration` with the *last finished* eval, and the script did not log
   `exploitability_iteration`. Rows were labelled 250 iterations late: the
   value shown at "500" is the 250-iteration eval (direct 0.04155), and so on.
   The corrected curve is below.
4. **Eval cost was misreported.** A full best-response eval takes 25 ms here,
   not ~2 s; the 2 s cadence was the poll interval. Throughput is ~78 it/s
   (the CFR steps), and evals are <1% of it.
5. **Final `exploitability = 0.0` is the engine clamping a negative NashConv.**
   At 4000 iterations `br_value_p0 + br_value_p1 = -0.000149`. That is inside
   the evaluator's `_negative_tolerance = 0.000229` (float32), so the engine
   reports `nashconv = 0.0`. The two best-response values are each ~0.744
   and are summed in float32 over 9.6M nodes, so the error near 1e-3 is about
   1e-4. Repeat evals of the same strategy are also not bitwise identical
   (non-deterministic GPU reductions). Below ~2e-3, the exploitability of this
   tree in float32 is noise-dominated. This explains the Phase 3 upticks
   (2250→2750, 3250→3750).

A float64 rerun (`probe_exploitability.py --mode direct --dtype float64`, log
`nan_probe_direct_f64.jsonl`; negative tolerance 1e-9) confirms point 5. Raw
NashConv stays positive at every eval (min 0.00167 at 4000 iterations), the
4000-iteration exploitability is **0.000833** instead of the float32 `0.0`,
the curve falls monotonically over the last evals (0.001225 → 0.001084 →
0.000944 → 0.000857 → 0.000833), and repeat evals agree to 2e-13. In float32,
1 of 17 repeat evals was bitwise equal.

**Wrapper fixes:** none were needed in the worker — it already drops
non-finite samples (Phase 3 review fix) and `exploitability_history` has one
entry per eval. Consumers should read `exploitability_history` (or pair
`exploitability` with `exploitability_iteration`), not the live `iteration`.
Use `solver_options.dtype = "float64"` when exploitability below ~2e-3 matters
on full-range trees.
