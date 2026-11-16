# Phase 1 — SolverEngine

Date: 2026-11-16
Branch: `phase1-engine`

## Gate

Phase 1 gate is **green**: the engine pytest suite passes on both RTX 5060 Ti GPUs.

Commands:

```bash
scripts/phase1/run_engine_tests.sh
.venv-engine/bin/python scripts/phase1/eval_api_check.py
```

Measured output:

```text
gpu0 13 passed in 3.79s
gpu1 13 passed in 3.64s
```

Raw pytest logs: `scripts/phase1/logs/pytest_gpu0.txt`, `scripts/phase1/logs/pytest_gpu1.txt`.
Eval/export check log: `scripts/phase1/logs/eval_api_check.txt`.

## Eval / strategy-export API check

Before building `SolverEngine`, the shipped eval modules were checked without stubbing or mocking `project_llm_opp_prior`:

```text
gpu_cfr.eval.exploitability: FAIL ImportError: Kuhn oracle requires project_llm_opp_prior on PYTHONPATH. Run project_gpu_cfr from the repository root or add the sibling project.
gpu_cfr.eval.best_response: OK
gpu_cfr.eval.strategy_export: OK
gpu_cfr.eval.metrics: OK
kuhn_export_entries=12
kuhn_exploitability=0.001895875
kuhn_profile_value_p0=-0.055537000
```

Decision: **do not implement `holdem_solver/eval` fallback for Phase 1**. `gpu_cfr.eval.best_response.TwoPlayerZeroSumEvaluator` imports and evaluates successfully on CPU and CUDA for Kuhn and for a tiny HUNL river spot, so `SolverEngine.evaluate()` uses it directly. `gpu_cfr.eval.exploitability` remains unusable because it requires the missing sibling `project_llm_opp_prior` package; nothing was stubbed.

## SolverEngine API

`holdem_solver.engine.SolverEngine` wraps `gpu_cfr.solvers.stagewise.StagewiseCFRSolver` and is the only package that imports `gpu_cfr`.

Public surface:

- `SolverEngine(game_spec=None, **solver_options)`
- `build(game_spec, **solver_options)`
- `solve(iterations=None, time_budget=None, target_exploitability=None, *, progress_every=None, progress_callback=None, chunk_iterations=None) -> SolveResult`
- `get_strategy() -> dict[str, numpy.ndarray]`
- `get_strategy_flat() -> torch.Tensor`
- `get_strategy_for_kuhn_oracle() -> dict`
- `evaluate() -> gpu_cfr.eval.best_response.BestResponseResult`
- `exploitability() -> float`
- `reset() -> SolverEngine`
- `close() -> None`
- context-manager support (`with SolverEngine(...) as engine:`)
- `holdem_solver.engine.estimate_vram_bytes(num_nodes, num_infoset_actions=0, *, dtype='float32', cuda_graph=True, safety_factor=1.1) -> int`

`SolveResult` reports `iterations_run`, `total_iterations`, `elapsed_seconds`, `exploitability`, `profile_value_p0`, `target_exploitability`, `target_reached`, and `time_budget_exhausted`. `SolverProgress` reports `iteration`, `iterations_run`, `elapsed_seconds`, and `exploitability`.

## Assumptions

These are the assumptions Phase 1 makes explicit; later phases should revisit any that become wrong.

1. **Game spec inputs.** `game_spec` may be:
   - a `gpu_cfr.efg.spec.GameSpec`,
   - a registry benchmark name such as `"kuhn_poker"`, `"hunl_river"`, or `"hunl_turn"`,
   - the alias `"kuhn"` for `build_kuhn_game()`, or
   - a zero-argument callable returning a `GameSpec`.
   Unknown strings raise from `gpu_cfr.games.registry.resolve_game_spec`.

2. **Build semantics.** `build()` closes any existing solver before constructing the new one. The constructor calls `build()` when `game_spec` is provided. `close()` clears solver/evaluator/game/config state and calls `gc.collect()` plus `torch.cuda.empty_cache()` when CUDA is available.

3. **Solver options.** Only these options are accepted: `variant`, `device`, `dtype`, `seed`, `cuda_graph`, `cuda_graph_unroll`, `alternating_updates`, `linear_averaging`, `payoff_storage`, `payoff_block_size`, `memory_max_bytes`, `memory_safety_factor`, and `iterations`. Unknown options raise `TypeError`. Engine defaults are `variant="cfr_plus"`, `device="cuda"`, `dtype="float32"`, `seed=42`, `cuda_graph=None` meaning “on for CUDA, off otherwise”, `cuda_graph_unroll=1`, `alternating_updates=False`, `linear_averaging=True`, `payoff_storage="dense"`, `payoff_block_size=4096`, `memory_max_bytes=None`, `memory_safety_factor=1.0`, and `iterations=1000`. These mirror `CFRConfig` except `device` defaults to CUDA for this engine and `cuda_graph` is auto-selected by device.

4. **Solve modes.** Exactly one of `iterations`, `time_budget`, or `target_exploitability` may be supplied; if none is supplied, `config.iterations` is used. `iterations` means **additional** iterations in that call, not a new total; call `reset()` for a fresh solve. `target_exploitability` must be positive and target mode has no iteration cap, so the target must be reachable in practice. `time_budget` uses `chunk_iterations` (default 10) between wall-clock checks, and both it and target mode can overshoot by up to one chunk plus the final exact exploitability evaluation.

5. **Progress callbacks.** A progress callback receives a `SolverProgress` snapshot whose `exploitability` is computed by an exact `TwoPlayerZeroSumEvaluator.evaluate()` call on the current average strategy. If `progress_every` is omitted while a callback is supplied, the default interval is 100 iterations. For fixed-iteration solves, exploitability is evaluated at report boundaries and once at the end; for target solves it is evaluated every chunk; for time-budget solves it is evaluated when the report interval is crossed and once at the end. Callback frequency is therefore tied to solver chunk boundaries, not a separate timer.

6. **Exploitability evaluator.** `evaluate()` uses `TwoPlayerZeroSumEvaluator(solver.game)` on `solver.average_strategy_flat()`. It assumes the compiled game is a two-player zero-sum EFG and that the evaluator’s depth-per-infoset structural requirement holds. `gpu_cfr.eval.exploitability` is not used because it imports the missing `project_llm_opp_prior` package. No `holdem_solver/eval` fallback was needed in Phase 1.

7. **Strategy exports.** `get_strategy()` returns the solver’s internal string-keyed average strategy; values are NumPy arrays aligned with `GameSpec.infosets[key].actions`. `get_strategy_flat()` returns the flat tensor consumed by the evaluator. `get_strategy_for_kuhn_oracle()` is Kuhn-only. `gpu_cfr.eval.strategy_export.export_internal_to_kuhn(engine.get_strategy())` was verified to produce 12 oracle-keyed entries for Kuhn.

8. **Reset.** `reset()` calls `StagewiseCFRSolver.reset()`, which preserves the compiled graph/CUDA-graph capture and zeroes accumulators in place. CUDA runs are not bitwise deterministic across graph replays; tests use a `2e-4` absolute/relative strategy tolerance, far above the observed ~`1e-5` run-to-run spread on this host.

9. **Close/VRAM.** `close()` drops Python references, runs `gc.collect()`, and calls `torch.cuda.empty_cache()`. A manual Kuhn check on GPU 0 showed allocated bytes fall to ~512 bytes after close; PyTorch may retain a small CUDA context reservation (~2 MiB) that cannot be returned to the driver.

10. **Lazy engine imports.** `holdem_solver.engine` is pure Python until first solver use: `_load_gpu_cfr()` inserts the submodule path, sets `sys.dont_write_bytecode = True`, and imports torch/`gpu_cfr` only when `build()`/`solve()`/`evaluate()`/strategy getters need them. Verified `.venv-api/bin/python` can `import holdem_solver.engine` with `torch` and `gpu_cfr` absent from `sys.modules`; the Python 3.15 API must still not call engine build/solve because GPU-CFR bindings are CPython 3.9-only. First engine use still sets `sys.dont_write_bytecode` process-wide to keep the read-only submodule's tracked `__pycache__` artifacts clean.

11. **VRAM estimator.** `estimate_vram_bytes` is a planning estimate calibrated from Phase 0 RTX 5060 Ti float32 measurements, not an allocator guarantee. It returns `max(allocated_model, reserved_model) * safety_factor` with default `safety_factor=1.1`. Coefficients are rounded and conservative: allocated ≈ `260 * num_nodes + 35 * num_infoset_actions + 32 KiB`; reserved ≈ `322 * num_nodes + 35 * num_infoset_actions + 10 MiB` for CUDA-graph solves or `+ 4 MiB` eager. `dtype="float64"` doubles the estimate because the float payload doubles while integer edge tables do not.

## Measured engine numbers

Kuhn (`kuhn_poker`, CFR+, seed 42):

- CPU, 2000 iterations: `exploitability=0.001895875`, `profile_value_p0=-0.055537000`.
- CUDA, 2000 iterations: `exploitability≈0.001895845`, `profile_value_p0≈-0.055537015`.
- Known Kuhn value for player 0 is `-1/18 = -0.055555556`; the 2000-iteration CFR+ profile value is within `2e-5` and exploitability is `< 0.005`.
- `export_internal_to_kuhn(engine.get_strategy())` returned 12 entries.

VRAM estimator examples from `estimate_vram_bytes`:

```text
kuhn        nodes=55      infoset_actions=24    estimate=11.019 MiB
hunl_river  nodes=137416  infoset_actions=1400  estimate=57.469 MiB
hunl_turn   nodes=434266  infoset_actions=38808 estimate=159.116 MiB
```

Phase 0 measured HUNL river peak reserved VRAM was 54 MiB with CUDA graph, so the river estimate is conservative. HUNL turn measured reserved was 142 MiB, so the turn estimate is also conservative.

## Files

- `holdem_solver/engine/__init__.py`
- `holdem_solver/engine/solver.py`
- `holdem_solver/engine/memory.py`
- `holdem_solver/__init__.py`
- `tests/test_solver_engine.py`
- `tests/test_memory.py`
- `pytest.ini`
- `scripts/phase1/run_engine_tests.sh`
- `scripts/phase1/eval_api_check.py`
- `scripts/phase1/logs/*`
