# Phase 2

Date: 2026-11-16

## Part A — process split

Branch: `phase2a-process-split`

### Gate

Part A gate is **green**: from `.venv-api` (Python 3.15.2) the client spawns a `.venv-engine` (Python 3.9.25) worker per GPU, round-trips a Kuhn solve on both RTX 5060 Ti cards, and each worker comes back cleanly after `SIGKILL`.

Command:

```bash
scripts/phase2/run_split_gate.sh
```

Measured output (`scripts/phase2/logs/roundtrip_gate.txt`, pytest logs alongside):

```text
engine gpu0 36 passed, 1 skipped in 4.30s
engine gpu1 36 passed, 1 skipped in 4.26s
api 47 passed in 30.11s
client python 3.15.2 (/home/aihub/repos/gpuThSlv/.venv-api/bin/python)
gpu0: worker pid=52324 python=3.9.25 torch=2.8.0+cu128 CUDA_VISIBLE_DEVICES=0 device=NVIDIA GeForce RTX 5060 Ti
  first solve : nodes=55 infosets=12 iterations=2000 exploitability=0.001895845 value_p0=-0.055537000 solve_s=0.301 peak_reserved_mib=8.0 row_sum_min=1.000000 row_sum_max=1.000000
  after SIGKILL: WorkerDiedError: solve: engine worker for gpu 0 is not reachable (ConnectionResetError: [Errno 104] Connection reset by peer)
  restarted: worker pid=52347 state=unbuilt shm_files=[]
  before rebuild: NotBuiltError: engine is not built; call build first
  second solve: nodes=55 infosets=12 iterations=2000 exploitability=0.001895711 value_p0=-0.055536956 solve_s=0.299 peak_reserved_mib=8.0 row_sum_min=1.000000 row_sum_max=1.000000
  nvidia-smi compute pids on gpu0: [52347] (killed 52324 present: False)
  shutdown: socket exists=False
gpu1: worker pid=52374 python=3.9.25 torch=2.8.0+cu128 CUDA_VISIBLE_DEVICES=1 device=NVIDIA GeForce RTX 5060 Ti
  first solve : nodes=55 infosets=12 iterations=2000 exploitability=0.001895957 value_p0=-0.055537015 solve_s=0.296 peak_reserved_mib=8.0 row_sum_min=1.000000 row_sum_max=1.000000
  after SIGKILL: WorkerDiedError: solve: engine worker for gpu 1 is not reachable (ConnectionResetError: [Errno 104] Connection reset by peer)
  restarted: worker pid=52399 state=unbuilt shm_files=[]
  before rebuild: NotBuiltError: engine is not built; call build first
  second solve: nodes=55 infosets=12 iterations=2000 exploitability=0.001895808 value_p0=-0.055537015 solve_s=0.285 peak_reserved_mib=8.0 row_sum_min=1.000000 row_sum_max=1.000000
  nvidia-smi compute pids on gpu1: [52399] (killed 52374 present: False)
  shutdown: socket exists=False
GATE OK
```

The one skipped engine test is `test_protocol_has_no_third_party_imports`, which needs `sys.stdlib_module_names` (Python 3.10+); it runs in the API suite.

### Packages

| Package | Runtime | Imports |
| --- | --- | --- |
| `holdem_solver/protocol/` | both | standard library only; 3.9 syntax |
| `holdem_solver/engine_worker/` | `.venv-engine` (3.9) | `protocol`, numpy, and (real engine only) `holdem_solver.engine` → torch, `gpu_cfr` |
| `holdem_solver/client/` | `.venv-api` (3.15) | `protocol`, numpy |

`tests/test_protocol.py` enforces the 3.9 rules by AST (no `match`, no `X | Y` annotations) for `protocol/`, `engine_worker/`, and `engine/`, and that `protocol/` imports nothing outside the standard library. `tests/test_client_fake.py` checks that importing the client loads no torch, `gpu_cfr`, `holdem_solver.engine`, or `holdem_solver.engine_worker` module.

`tests/conftest.py` makes each venv collect only what it can import: client tests are skipped under 3.9 and `test_solver_engine.py` under 3.15.

### Wire protocol (version 1)

- Transport: Unix stream socket, JSON-RPC 2.0, one JSON object per line, named params only. Messages are capped at 16 MiB; anything bulk goes through `/dev/shm`.
- Default paths: socket `/tmp/holdem_solver_<uid>/gpu<N>.sock` (plus `.sock.lock` and `.log`), arrays `/dev/shm/holdem_solver_<uid>/gpu<N>/`. Overridable with `HOLDEM_SOLVER_RUNTIME_DIR` / `HOLDEM_SOLVER_SHM_DIR` or per client/worker arguments. Directories are `0700`.
- Array reference: `{"__npy__": "<path>", "shape": [...], "dtype": "<numpy dtype>"}`. The receiver refuses paths outside the worker's shm directory, loads with `allow_pickle=False`, checks shape/dtype, and deletes the file.

| Method | Params | Result |
| --- | --- | --- |
| `ping` | — | protocol version, pid, gpu, `CUDA_VISIBLE_DEVICES`, python/torch versions, device name, state, VRAM |
| `build` | `game`, `options` | game name, node/infoset/infoset-action counts, estimated VRAM, device, build seconds, VRAM |
| `solve` | `iterations` \| `time_budget` \| `target_exploitability`, `progress_every`, `chunk_iterations` | `job_id`; returns immediately |
| `solve_wait` | `timeout` (≤ 60 s) | `done`, `state`, and the `SolveResult` fields + VRAM when done |
| `progress` | — | state, iteration, iterations run, targets, elapsed, last evaluated exploitability, `cancel_requested`, error |
| `cancel` | — | `cancel_requested` |
| `get_strategy` | — | array refs `keys` (bytes), `offsets` (int64), `values` (float), plus `num_infosets`, `iteration` |
| `reset` | — | `iteration` |
| `close` | — | state, VRAM (solver released, worker stays up) |
| `shutdown` | — | pid (worker exits) |

States: `unbuilt`, `idle`, `solving`, `done`, `cancelled`, `failed`. Error codes: standard JSON-RPC `-32700/-32600/-32601/-32602/-32603`, plus `-32000` engine error (with remote type and traceback), `-32001` not built, `-32002` busy, `-32003` no job.

### Worker

`python -m holdem_solver.engine_worker --gpu N [--engine real|fake] [--socket PATH] [--shm-dir DIR] [--parent-pid PID]`

- One worker per GPU. `--gpu N` sets `CUDA_VISIBLE_DEVICES=N` before torch is imported (the client also sets it in the child environment), so the worker always sees its card as `cuda:0`.
- The real engine imports torch and `gpu_cfr` before the socket is bound: a worker that answers `ping` is usable.
- All engine calls run on one dedicated thread, so CUDA graphs are captured and replayed on the same thread. Socket threads only read job state, which keeps `progress`/`cancel` responsive during a solve. `build`, `solve`, `get_strategy`, and `reset` return *busy* while a solve or build is running.
- A `flock` on `<socket>.lock` guarantees one worker per socket. The lock dies with the process, so after `SIGKILL` the next worker takes the lock, removes the stale socket, and clears leftover `.npy` files.
- `SIGTERM`/`SIGINT`/`shutdown` cancel a running solve, release the engine, and remove the socket and shm files. With `--parent-pid` the worker also exits when its parent goes away.
- Engine exceptions are returned as RPC errors; the worker stays up.

### Client

`holdem_solver.client.EngineClient(gpu, *, engine="real", spawn=True, auto_restart=False, ...)`

- `build(game, **options) -> BuildInfo`
- `solve(iterations | time_budget | target_exploitability, *, progress_every, chunk_iterations, wait=True, timeout, on_progress) -> SolveResult`
- `wait(timeout, on_progress) -> SolveResult`, `progress() -> Progress`, `cancel() -> bool`
- `get_strategy(flat=False) -> dict[str, ndarray] | Strategy`
- `reset()`, `close()` (release the solver; same meaning as `SolverEngine.close`)
- `ping()`, `start()`, `restart()`, `shutdown()`, `is_alive()`, `worker_pid`; `with EngineClient(...)` shuts the worker down on exit.

Each call opens its own connection, so `progress()` and `cancel()` work from another thread while `solve()` blocks.

### FakeEngine

`holdem_solver.engine_worker.FakeEngine` has the `SolverEngine` call surface but needs no torch, CUDA, or `gpu_cfr`. It does **not** run CFR: exploitability is `0.5 / sqrt(1 + iterations)` and the strategy is a deterministic function of the iteration count. `EngineClient(None, engine="fake")` runs it in a worker under the current interpreter, so the client tests need no GPU. Build options `fake_iter_seconds`, `fake_infosets`, `fake_actions`, and `fake_fail_solve` exist to test progress, cancel, bulk transfer, and error paths.

### Decisions and assumptions

1. **`solve` is asynchronous on the wire.** The RPC starts a job; the client long-polls `solve_wait` in 2 s slices. No connection is held for a whole solve, and a dead worker is noticed within one slice.
2. **Cancel granularity is one solver chunk.** `SolverEngine.solve` gained `should_stop` (polled between chunks) and `SolveResult.cancelled`. With `should_stop`, fixed-iteration solves step in chunks of `chunk_iterations` (default 100) instead of one call; with `progress_every` and no `chunk_iterations`, the chunk is `progress_every`. A cancelled solve keeps its iterations and can be continued.
3. **Progress is polled, not pushed.** `progress().iteration` is read live from the solver. Exploitability is only evaluated mid-solve when `progress_every` is given, because each evaluation is an exact best-response pass.
4. **A dead worker is an error, not a silent retry.** The call that notices raises `WorkerDiedError`; all engine state is lost. `restart()` (or `auto_restart=True`, which restarts before raising) gives a fresh *unbuilt* worker, and calls fail with `NotBuiltError` until `build` is called again.
5. **The client owns files it receives.** It deletes each `.npy` after loading. If a strategy is never collected, the worker replaces it on the next `get_strategy` and clears everything on `close`, exit, and startup.
6. **Strategy layout.** One flat `values` array plus `offsets` and `keys`, not one file per infoset. Keys are UTF-8 bytes.
7. **`holdem_solver/engine/` stays where Phase 1 put it.** AGENTS.md says only `engine_worker/` may import `gpu_cfr`; the import still lives in `holdem_solver/engine/solver.py`, which only `engine_worker/real.py` imports. Moving it under `engine_worker/` is a rename of Phase 1 code and was left out of this change.
8. **`build` accepts a name or a JSON object.** Only registry names work today; the object form is reserved for Part B's `SpotConfig`.

### Measured numbers

Kuhn (`kuhn_poker`, CFR+, seed 42, 2000 iterations) through client + worker: exploitability `0.001895711`–`0.001895957`, `profile_value_p0` `-0.055536956`–`-0.055537015` (known value `-1/18 = -0.055555556`), solve wall time `0.285`–`0.301 s`, peak reserved VRAM `8.0 MiB`. These match the in-process Phase 1 numbers.
