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

## Part B — spot builder

### What the engine builders accept

Inspected with `help()` in `.venv-engine`; the full output is in `scripts/phase2/logs/hunl_api_help.txt`. Both builders are configurable, not fixed presets, so the spot builder calls them directly and the EFG-compiler route was not needed.

```
build_hunl_river_game(board, pot=20.0, stack=100.0, bet_fractions=(0.5, 1.0),
                      num_hands_per_player=100, seed=0, hands0=None, hands1=None, name="hunl_river")
build_hunl_turn_game(board, pot=20.0, stack=100.0, bet_fractions=(0.5, 1.0),
                     num_hands_per_player=12, seed=0, hands0=None, hands1=None, name="hunl_turn")
```

| Parameter | Accepted | Notes |
| --- | --- | --- |
| Board | Yes | Card strings; exactly 5 for the river, exactly 4 for the turn. No flop or preflop builder. |
| Ranges | Yes, as hand lists | `hands0` / `hands1` are explicit two-card hands. Without them the builder samples `num_hands_per_player` random hands from `seed`. |
| Range weights | Not in the builder | The deal is uniform over non-conflicting hand pairs. Weights are applied afterwards with the solver's `set_root_ranges`. |
| Pot | Yes | Starting pot, split evenly as each player's sunk contribution. |
| Stack | Yes | One effective stack for both players; bets are capped at it. |
| Bet sizes | Yes | Pot fractions, the same list for both players and, on the turn, both streets. |
| Raises | No | The grammar is check / bet / fold / call only. |
| Per-player or per-street sizes, donk-only lines | No | Would need a hand-written `GameSpec` through the EFG compiler. |
| Dead cards | No | Handled by filtering the ranges; on the turn the builder still deals all 44 non-board river cards, so `SpotConfig` rejects turn dead cards. |

Blockers are handled by the builder itself: a hand pair that shares a card is not dealt. The turn builder adds a second chance layer dealing each remaining river card after every turn line that continues.

### SpotConfig

`holdem_solver/protocol/spot.py` (stdlib only, usable from both runtimes) and `holdem_solver/protocol/cards.py`.

```python
from holdem_solver.client import EngineClient, SpotConfig

spot = SpotConfig("KsJsTh7d2c", "QQ+, AKs, AKo:0.5", "TT+, AQ",
                  pot=20, stack=100, bet_sizes="standard", all_in=True)
with EngineClient(0) as client:
    built = client.build(spot)       # built.spot, built.num_nodes, built.estimated_vram_bytes
    result = client.solve(iterations=2000)
```

- Board: 4 cards (turn) or 5 (river), as a string or a list.
- Ranges: `AA`, `AKs`, `AKo`, `AK`, `TT+`, `A5s+`, `77-TT`, `A2s-A5s`, explicit combos (`AhKh`), `*`, each with an optional `:weight`. Later tokens override earlier ones.
- Bet sizes: a named set or a list of pot fractions; `all_in=True` adds the stack as a size.

| Set | Pot fractions |
| --- | --- |
| `third` | 1/3 |
| `half` | 1/2 |
| `pot` | 1 |
| `standard` | 1/2, 1 |
| `small_big` | 1/3, 3/4 |
| `wide` | 1/3, 2/3, 1 |
| `polar` | 3/4, 3/2 |
| `overbet` | 1/2, 1, 2 |

- Blockers: combos that use a board or dead card are removed from each range (`num_removed`); hand pairs that share a card are not dealt (`num_blocked_pairs`, `blocked_opponent_combos`). A spot with no legal deal is rejected.
- `max_nodes` (default 5,000,000): the worker computes the exact node count from a one-deal tree, `1 + deals * (single_deal_nodes - 1)`, and refuses before building anything larger. `None` disables the check.
- Worker side: `holdem_solver/engine_worker/spot_builder.py` calls the engine builder; `RealEngine.build` accepts the spot dict and applies range weights. `FakeEngine` validates and resolves a spot without building a tree.

### Known-answer tests

`tests/test_spot_builder.py` (engine runtime) and `tests/test_client_gpu.py::test_spot_known_answers_through_the_worker` (through the worker, both GPUs). The spots are nuts-or-air against one bluff-catcher, where pot `P`, bet `B`, and nut fraction `v` give player 0 a value of `v * (P + B*P/(P+B)) - P/2` and a calling frequency of `P/(P+B)`.

| Spot | Expected value (p0) | Checked |
| --- | --- | --- |
| River, `v=1/4`, pot-sized bet | -1.25 | value ±0.01, call 1/2, bluff:value 1:2 |
| River, 1/2-pot, pot and all-in available | 1/4 * (10 + 20/3) - 5 ≈ -0.833 | value ±0.02, call 1/3 against all-in |
| River, air weighted 3x (`4c3c:3`) | -1.25 | value ±0.01, also after `reset()` |
| River, same hands unweighted (`v=1/2`) | 2.5 | value ±0.01 |
| River, opponent blocks the nuts | -5 | value ±0.001, exploitability ≈ 0 |
| River, nut card dead | -5 | value ±0.001 |
| Turn, two pot-sized bets, static board | 0.625 | value ±0.03 at 40,000 iterations, call 1/2, turn bluff 5/12 per air hand |

### Node counts and VRAM

Measured by `scripts/phase2/measure_spots.py` from `.venv-api`, one fresh worker per spot, 200 CFR+ iterations each; raw output in `scripts/phase2/logs/spot_measurements.json`. "Est." is `estimate_vram_bytes()`; the peaks are `torch.cuda.max_memory_reserved/allocated` in the worker after the solve. Boards: river `Ks Js Th 7d 2c`, turn `Ks Js Th 7d` (toy turn `As Ks 7s 2s`); pot 20, stack 100 unless toy. "broad" is `22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo`.

| Spot | GPU | Hands (p0 x p1) | Deals | Nodes | Infosets | Build s | Est. VRAM MiB | Peak reserved MiB | Peak allocated MiB | Est. / peak | it/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| river-toy | 0 | 4 x 1 | 4 | 37 | 10 | 0.4 | 11.0 | 8.0 | 0.1 | 1.38 | 1099 |
| river-toy | 1 | 4 x 1 | 4 | 37 | 10 | 0.4 | 11.0 | 8.0 | 0.1 | 1.38 | 1154 |
| river-narrow | 0 | 27 x 37 | 726 | 10891 | 192 | 0.5 | 14.7 | 10.0 | 3.6 | 1.47 | 991 |
| river-narrow | 1 | 27 x 37 | 726 | 10891 | 192 | 0.5 | 14.7 | 10.0 | 3.6 | 1.47 | 888 |
| river-broad-standard | 0 | 209 x 209 | 38202 | 573031 | 1254 | 5.8 | 204.7 | 228.0 | 187.6 | 0.90 | 181 |
| river-broad-standard | 1 | 209 x 209 | 38202 | 573031 | 1254 | 6.0 | 204.7 | 228.0 | 187.6 | 0.90 | 168 |
| river-broad-wide-allin | 0 | 209 x 209 | 38202 | 1031455 | 2090 | 10.2 | 359.6 | 402.0 | 341.0 | 0.89 | 104 |
| river-broad-wide-allin | 1 | 209 x 209 | 38202 | 1031455 | 2090 | 10.9 | 359.6 | 402.0 | 341.0 | 0.89 | 101 |
| river-full-pot | 0 | 1081 x 499 | 494010 | 4446091 | 3160 | 45.5 | 1513.1 | 1576.0 | 1432.5 | 0.96 | 22 |
| river-full-pot | 1 | 1081 x 499 | 494010 | 4446091 | 3160 | 46.0 | 1513.1 | 1576.0 | 1432.5 | 0.96 | 26 |
| turn-toy | 0 | 4 x 1 | 4 | 4789 | 1342 | 0.6 | 12.7 | 8.0 | 1.7 | 1.59 | 969 |
| turn-toy | 1 | 4 x 1 | 4 | 4789 | 1342 | 0.6 | 12.7 | 8.0 | 1.7 | 1.59 | 903 |
| turn-narrow-standard | 0 | 18 x 25 | 339 | 1123786 | 29799 | 11.8 | 393.2 | 434.0 | 370.7 | 0.91 | 94 |
| turn-narrow-standard | 1 | 18 x 25 | 339 | 1123786 | 29799 | 11.8 | 393.2 | 434.0 | 370.7 | 0.91 | 101 |
| turn-pairs-standard | 0 | 33 x 43 | 1128 | 3739321 | 52668 | 40.5 | 1278.6 | 1366.0 | 1228.4 | 0.94 | 34 |
| turn-pairs-standard | 1 | 33 x 43 | 1128 | 3739321 | 52668 | 40.2 | 1278.6 | 1366.0 | 1228.4 | 0.94 | 29 |
| turn-pairs-pot-allin | 0 | 33 x 43 | 1128 | 2548153 | 38684 | 27.7 | 874.7 | 952.0 | 828.3 | 0.92 | 48 |
| turn-pairs-pot-allin | 1 | 33 x 43 | 1128 | 2548153 | 38684 | 25.5 | 874.7 | 952.0 | 828.3 | 0.92 | 47 |

- The estimate is 4–11% below the measured peak reserved for every spot over 500k nodes, and above it (dominated by the fixed CUDA floor) for small ones. Budget about 1.15x the estimate.
- Both GPUs give identical node counts and peaks.
- Memory follows node count: about 350 bytes of peak reserved per node across the large spots, so 16 GiB is roughly 40M nodes. That extrapolation is not measured.
- Trees grow with deals, not range width alone: a full-range river with one bet size is 4.4M nodes, and a turn with 33 x 43 hands and two sizes is 3.7M because each turn line fans out over 44 rivers.
- Build time is CPU-bound tree construction in the engine, about 10 s per million nodes.

### Limits

- No raises, no flop or preflop, one bet-size list for both players and streets: these are limits of the engine builders, not of `SpotConfig`.
- Turn dead cards are rejected (see the table above).
- 200 iterations is enough to measure memory, not to converge; the exploitabilities in the JSON are not solution quality.
