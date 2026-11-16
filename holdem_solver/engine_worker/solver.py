"""SolverEngine wrapper around GPU-CFR's StagewiseCFRSolver.

GPU-CFR imports are lazy: importing this package does not load the
CPython 3.9-only bindings. ``build()``, ``solve()``, ``evaluate()``, and the
strategy getters load them on first use, so this package must still only be
imported by the Python 3.9 engine runtime.
"""

from __future__ import annotations

import gc
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from .memory import estimate_vram_bytes

_GPU_CFR: Any = None


def _load_gpu_cfr() -> Any:
    """Import GPU-CFR bindings on first engine use and cache them."""
    global _GPU_CFR
    if _GPU_CFR is not None:
        return _GPU_CFR

    gpu_cfr_root = Path(__file__).resolve().parents[2] / "third_party" / "GPU-CFR"
    if gpu_cfr_root.is_dir() and str(gpu_cfr_root) not in sys.path:
        sys.path.insert(0, str(gpu_cfr_root))

    # The vendored GPU-CFR submodule is read-only and tracks __pycache__
    # artifacts; keep imports from rewriting them in the parent checkout.
    sys.dont_write_bytecode = True

    import torch
    from gpu_cfr.config import CFRConfig, DeviceConfig, MemoryConfig, PayoffConfig
    from gpu_cfr.efg.spec import GameSpec
    from gpu_cfr.games.kuhn import build_kuhn_game
    from gpu_cfr.games.registry import resolve_game_spec
    from gpu_cfr.solvers.stagewise import StagewiseCFRSolver
    from gpu_cfr.eval.best_response import TwoPlayerZeroSumEvaluator

    _GPU_CFR = SimpleNamespace(
        torch=torch,
        CFRConfig=CFRConfig,
        DeviceConfig=DeviceConfig,
        MemoryConfig=MemoryConfig,
        PayoffConfig=PayoffConfig,
        GameSpec=GameSpec,
        build_kuhn_game=build_kuhn_game,
        resolve_game_spec=resolve_game_spec,
        StagewiseCFRSolver=StagewiseCFRSolver,
        TwoPlayerZeroSumEvaluator=TwoPlayerZeroSumEvaluator,
    )
    return _GPU_CFR


@dataclass(frozen=True)
class SolverProgress:
    """Progress snapshot passed to ``solve(progress_callback=...)``."""

    iteration: int
    iterations_run: int
    elapsed_seconds: float
    exploitability: float


@dataclass(frozen=True)
class SolveResult:
    """Result of one ``SolverEngine.solve`` call."""

    iterations_run: int
    total_iterations: int
    elapsed_seconds: float
    exploitability: float
    profile_value_p0: float
    target_exploitability: Optional[float]
    target_reached: bool
    time_budget_exhausted: bool
    cancelled: bool = False


_SOLVER_OPTION_DEFAULTS = {
    "variant": "cfr_plus",
    "device": "cuda",
    "dtype": "float32",
    "seed": 42,
    "cuda_graph": None,
    "cuda_graph_unroll": 1,
    "alternating_updates": False,
    "linear_averaging": True,
    "payoff_storage": "dense",
    "payoff_block_size": 4096,
    "memory_max_bytes": None,
    "memory_safety_factor": 1.0,
    "iterations": 1000,
}

_SOLVER_OPTION_KEYS = frozenset(_SOLVER_OPTION_DEFAULTS)


def _resolve_game_spec(game_spec: Any) -> Any:
    bindings = _load_gpu_cfr()
    game_spec_cls = bindings.GameSpec
    if isinstance(game_spec, game_spec_cls):
        return game_spec
    if isinstance(game_spec, str):
        name = game_spec.strip()
        if not name:
            raise ValueError("game_spec string must not be empty")
        if name == "kuhn":
            return bindings.build_kuhn_game()
        return bindings.resolve_game_spec(name)
    if callable(game_spec):
        resolved = game_spec()
        if isinstance(resolved, game_spec_cls):
            return resolved
        raise TypeError("game_spec callable must return a gpu_cfr.efg.spec.GameSpec")
    raise TypeError(
        "game_spec must be a GameSpec, benchmark game name, or zero-argument "
        "callable returning a GameSpec"
    )


class SolverEngine:
    """High-level wrapper around ``StagewiseCFRSolver``.

    ``solve(iterations=N)`` runs ``N`` additional iterations from the current
    state; call ``reset()`` first for a fresh solve. ``time_budget`` and
    ``target_exploitability`` are checked between solver chunks, so they can
    overshoot by up to one chunk (plus one final exploitability evaluation).
    """

    def __init__(self, game_spec: Any = None, **solver_options: Any) -> None:
        self._solver_options = dict(solver_options)
        unknown = sorted(set(self._solver_options) - _SOLVER_OPTION_KEYS)
        if unknown:
            raise TypeError("unknown solver options: %s" % ", ".join(unknown))
        self._solver: Optional[Any] = None
        self._evaluator: Optional[Any] = None
        self._game_spec: Optional[Any] = None
        self._config: Optional[Any] = None
        self._estimated_vram_bytes = 0
        if game_spec is not None:
            self.build(game_spec)

    def build(self, game_spec: Any, **solver_options: Any) -> "SolverEngine":
        """Build a new solver, replacing and closing any existing one."""
        self.close()
        options = dict(self._solver_options)
        options.update(solver_options)
        unknown = sorted(set(options) - _SOLVER_OPTION_KEYS)
        if unknown:
            raise TypeError("unknown solver options: %s" % ", ".join(unknown))

        spec = _resolve_game_spec(game_spec)
        config = self._make_config(spec, options)
        solver = _load_gpu_cfr().StagewiseCFRSolver(spec, config)

        self._game_spec = spec
        self._config = config
        self._solver = solver
        self._evaluator = None
        self._estimated_vram_bytes = self._estimate_for_spec(spec, options)
        return self

    @staticmethod
    def _make_config(spec: Any, options: Dict[str, Any]) -> Any:
        bindings = _load_gpu_cfr()
        device = str(options.get("device", _SOLVER_OPTION_DEFAULTS["device"]))
        cuda_graph = options.get("cuda_graph", _SOLVER_OPTION_DEFAULTS["cuda_graph"])
        if cuda_graph is None:
            cuda_graph = device.startswith("cuda")
        return bindings.CFRConfig(
            game=spec.name,
            variant=str(options.get("variant", _SOLVER_OPTION_DEFAULTS["variant"])),
            iterations=int(options.get("iterations", _SOLVER_OPTION_DEFAULTS["iterations"])),
            alternating_updates=bool(
                options.get("alternating_updates", _SOLVER_OPTION_DEFAULTS["alternating_updates"])
            ),
            linear_averaging=bool(
                options.get("linear_averaging", _SOLVER_OPTION_DEFAULTS["linear_averaging"])
            ),
            seed=int(options.get("seed", _SOLVER_OPTION_DEFAULTS["seed"])),
            device=bindings.DeviceConfig(
                device=device,
                dtype=str(options.get("dtype", _SOLVER_OPTION_DEFAULTS["dtype"])),
            ),
            payoff=bindings.PayoffConfig(
                storage=str(options.get("payoff_storage", _SOLVER_OPTION_DEFAULTS["payoff_storage"])),
                block_size=int(options.get("payoff_block_size", _SOLVER_OPTION_DEFAULTS["payoff_block_size"])),
            ),
            memory=bindings.MemoryConfig(
                max_bytes=options.get("memory_max_bytes", _SOLVER_OPTION_DEFAULTS["memory_max_bytes"]),
                safety_factor=float(
                    options.get("memory_safety_factor", _SOLVER_OPTION_DEFAULTS["memory_safety_factor"])
                ),
            ),
            cuda_graph=bool(cuda_graph),
            cuda_graph_unroll=int(
                options.get("cuda_graph_unroll", _SOLVER_OPTION_DEFAULTS["cuda_graph_unroll"])
            ),
        )

    @staticmethod
    def _estimate_for_spec(spec: Any, options: Dict[str, Any]) -> int:
        num_nodes = len(spec.nodes)
        num_infoset_actions = sum(len(infoset.actions) for infoset in spec.infosets.values())
        dtype = str(options.get("dtype", _SOLVER_OPTION_DEFAULTS["dtype"]))
        device = str(options.get("device", _SOLVER_OPTION_DEFAULTS["device"]))
        cuda_graph = options.get("cuda_graph", _SOLVER_OPTION_DEFAULTS["cuda_graph"])
        if cuda_graph is None:
            cuda_graph = device.startswith("cuda")
        return estimate_vram_bytes(
            num_nodes,
            num_infoset_actions,
            dtype=dtype,
            cuda_graph=bool(cuda_graph),
        )

    def _require_solver(self) -> Any:
        if self._solver is None:
            raise RuntimeError("SolverEngine is not built; call build() first")
        return self._solver

    def _get_evaluator(self) -> Any:
        solver = self._require_solver()
        if self._evaluator is None:
            evaluator_cls = _load_gpu_cfr().TwoPlayerZeroSumEvaluator
            self._evaluator = evaluator_cls(solver.game)
        return self._evaluator

    @staticmethod
    def _sync(solver: Any) -> None:
        if solver.device.type == "cuda":
            _load_gpu_cfr().torch.cuda.synchronize(solver.device)

    @property
    def is_built(self) -> bool:
        return self._solver is not None

    @property
    def game_spec(self) -> Optional[Any]:
        return self._game_spec

    @property
    def compiled_game(self) -> Any:
        solver = self._require_solver()
        return solver.game

    @property
    def config(self) -> Optional[Any]:
        return self._config

    @property
    def iterations(self) -> int:
        solver = self._solver
        return 0 if solver is None else int(solver.iterations)

    @property
    def device(self) -> Optional[str]:
        solver = self._solver
        return None if solver is None else str(solver.device)

    @property
    def estimated_vram_bytes(self) -> int:
        return self._estimated_vram_bytes

    def solve(
        self,
        iterations: Optional[int] = None,
        time_budget: Optional[float] = None,
        target_exploitability: Optional[float] = None,
        *,
        progress_every: Optional[int] = None,
        progress_callback: Optional[Callable[[SolverProgress], None]] = None,
        chunk_iterations: Optional[int] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> SolveResult:
        """Run CFR until an iteration, wall-clock, or exploitability target.

        Exactly one of ``iterations``, ``time_budget``, and
        ``target_exploitability`` should be supplied; if none is supplied,
        ``config.iterations`` is used. ``iterations`` is the number of
        additional iterations in this call, not a new total. Target mode has
        no iteration cap, so the target must be reachable in practice; time
        budget uses ``chunk_iterations`` (default 10) between wall-clock checks.

        ``should_stop`` is polled between solver chunks; when it returns true
        the solve stops early and the result has ``cancelled=True``. Supplying
        it makes fixed-iteration solves step in chunks of ``chunk_iterations``
        (default 100) instead of one call.
        """
        solver = self._require_solver()
        modes = [iterations is not None, time_budget is not None, target_exploitability is not None]
        if sum(modes) > 1:
            raise ValueError("solve accepts only one of iterations, time_budget, target_exploitability")
        if not any(modes):
            iterations = int(self._config.iterations)  # type: ignore[union-attr]

        if iterations is not None:
            iterations = int(iterations)
            if iterations <= 0:
                raise ValueError("iterations must be positive")
        if time_budget is not None:
            time_budget = float(time_budget)
            if time_budget < 0:
                raise ValueError("time_budget must be non-negative")
        if target_exploitability is not None:
            target_exploitability = float(target_exploitability)
            if target_exploitability <= 0:
                raise ValueError("target_exploitability must be positive")
        if progress_every is not None:
            progress_every = int(progress_every)
            if progress_every <= 0:
                raise ValueError("progress_every must be positive")
        if chunk_iterations is not None:
            chunk_iterations = int(chunk_iterations)
            if chunk_iterations <= 0:
                raise ValueError("chunk_iterations must be positive")
        if progress_callback is not None and not callable(progress_callback):
            raise TypeError("progress_callback must be callable")
        if should_stop is not None and not callable(should_stop):
            raise TypeError("should_stop must be callable")

        # Progress callbacks are the only reason to evaluate exploitability
        # mid-solve for fixed-iteration runs; target mode evaluates every chunk.
        report_every = progress_every if progress_callback is not None else None
        if progress_callback is not None and report_every is None:
            report_every = 100

        start_time = time.perf_counter()
        run = 0
        last_report_run = 0
        last_eval_result: Optional[Any] = None
        last_eval_total: Optional[int] = None
        target_reached = False
        budget_exhausted = False
        cancelled = False

        def stop_requested() -> bool:
            return should_stop is not None and bool(should_stop())

        def elapsed() -> float:
            return time.perf_counter() - start_time

        def evaluate_now() -> Any:
            nonlocal last_eval_result, last_eval_total
            result = self.evaluate()
            last_eval_result = result
            last_eval_total = int(solver.iterations)
            return result

        def report(result: Any, force: bool = False) -> None:
            nonlocal last_report_run
            if progress_callback is None or last_report_run == run:
                return
            if not force and report_every is not None and run - last_report_run < report_every:
                return
            progress_callback(
                SolverProgress(
                    iteration=int(solver.iterations),
                    iterations_run=run,
                    elapsed_seconds=elapsed(),
                    exploitability=float(result.exploitability),
                )
            )
            last_report_run = run

        if iterations is not None:
            remaining = iterations
            if progress_callback is None and should_stop is None:
                solver.step(remaining)
                run = remaining
            else:
                if progress_callback is None:
                    chunk = chunk_iterations or 100
                else:
                    chunk = chunk_iterations or report_every or 100
                while remaining > 0:
                    if stop_requested():
                        cancelled = True
                        break
                    n = min(chunk, remaining)
                    solver.step(n)
                    remaining -= n
                    run += n
                    if progress_callback is not None and run - last_report_run >= (report_every or chunk):
                        report(evaluate_now())
            if last_eval_total != int(solver.iterations):
                final_result = evaluate_now()
            else:
                final_result = last_eval_result
            report(final_result, force=True)

        elif target_exploitability is not None:
            initial = self.evaluate()
            last_eval_result = initial
            last_eval_total = int(solver.iterations)
            if initial.exploitability <= target_exploitability:
                target_reached = True
                final_result = initial
            else:
                chunk = chunk_iterations or progress_every or 100
                while True:
                    if stop_requested():
                        cancelled = True
                        final_result = last_eval_result
                        break
                    solver.step(chunk)
                    run += chunk
                    result = evaluate_now()
                    report(result)
                    if result.exploitability <= target_exploitability:
                        target_reached = True
                        final_result = result
                        break
                report(final_result, force=True)

        else:  # time_budget is not None
            budget = float(time_budget)
            step_chunk = chunk_iterations or 10
            if progress_callback is not None and report_every is not None:
                step_chunk = min(step_chunk, report_every)
            while elapsed() < budget:
                if stop_requested():
                    cancelled = True
                    break
                solver.step(step_chunk)
                run += step_chunk
                self._sync(solver)
                if progress_callback is not None and report_every is not None and run - last_report_run >= report_every:
                    report(evaluate_now())
                if elapsed() >= budget:
                    budget_exhausted = True
                    break
            if last_eval_total != int(solver.iterations):
                final_result = evaluate_now()
            else:
                final_result = last_eval_result
            report(final_result, force=True)
            budget_exhausted = elapsed() >= budget

        if final_result is None:
            raise RuntimeError("solve finished without evaluating a strategy")
        return SolveResult(
            iterations_run=run,
            total_iterations=int(solver.iterations),
            elapsed_seconds=elapsed(),
            exploitability=float(final_result.exploitability),
            profile_value_p0=float(final_result.profile_value_p0),
            target_exploitability=target_exploitability,
            target_reached=target_reached,
            time_budget_exhausted=budget_exhausted,
            cancelled=cancelled,
        )

    def get_strategy(self) -> Dict[str, Any]:
        """Return the average strategy keyed by internal infoset string.

        Each value is a NumPy array aligned with the action order in the
        corresponding ``GameSpec.infosets[key].actions`` tuple.
        """
        solver = self._require_solver()
        return solver.average_strategy()

    def get_strategy_flat(self) -> Any:
        """Return the flat per-infoset-action average strategy tensor."""
        solver = self._require_solver()
        return solver.average_strategy_flat()

    def strategy_action_names(self) -> Dict[str, Any]:
        """``{infoset key: action names}`` in the engine's action order."""
        spec = self._game_spec
        if spec is None:
            raise RuntimeError("SolverEngine is not built; call build() first")
        return {
            key: [getattr(action, "name", str(action)) for action in infoset.actions]
            for key, infoset in spec.infosets.items()
        }

    def get_strategy_for_kuhn_oracle(self) -> Dict[Any, Any]:
        """Return Kuhn-oracle-keyed average strategy (Kuhn games only)."""
        solver = self._require_solver()
        return solver.average_strategy_for_kuhn_oracle()

    def evaluate(self) -> Any:
        """Evaluate the current average strategy exactly.

        Uses ``gpu_cfr.eval.best_response.TwoPlayerZeroSumEvaluator``; it does
        not require the missing ``project_llm_opp_prior`` sibling package.
        """
        solver = self._require_solver()
        evaluator = self._get_evaluator()
        return evaluator.evaluate(solver.average_strategy_flat())

    def exploitability(self) -> float:
        """Return current exploitability (NashConv / 2)."""
        return float(self.evaluate().exploitability)

    def reset(self) -> "SolverEngine":
        """Reset accumulators and iteration count in place, keeping the graph."""
        solver = self._require_solver()
        solver.reset()
        return self

    def close(self) -> None:
        """Release the solver, evaluator, and cached CUDA memory."""
        solver = self._solver
        evaluator = self._evaluator
        self._solver = None
        self._evaluator = None
        self._game_spec = None
        self._config = None
        self._estimated_vram_bytes = 0
        if solver is not None or evaluator is not None:
            del solver, evaluator
            gc.collect()
            torch = _load_gpu_cfr().torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def __enter__(self) -> "SolverEngine":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
