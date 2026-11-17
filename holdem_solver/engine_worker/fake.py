"""GPU-free stand-in for ``SolverEngine`` with the same call surface.

Used to exercise the client and the worker plumbing without torch, CUDA, or
``gpu_cfr``. It does not run CFR: the "strategy" and "exploitability" are
deterministic functions of the iteration count.
"""

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from holdem_solver.protocol.spot import SpotConfig

_KUHN_INFOSETS = 12
_KUHN_NODES = 55


@dataclass(frozen=True)
class FakeSolveResult:
    iterations_run: int
    total_iterations: int
    elapsed_seconds: float
    exploitability: float
    profile_value_p0: float
    target_exploitability: Optional[float]
    target_reached: bool
    time_budget_exhausted: bool
    cancelled: bool = False


@dataclass(frozen=True)
class FakeProgress:
    iteration: int
    iterations_run: int
    elapsed_seconds: float
    exploitability: float


class OutOfMemoryError(RuntimeError):
    """Stands in for ``torch.cuda.OutOfMemoryError`` (matched by class name)."""


class FakeEngine:
    """Deterministic fake engine.

    Build options (all optional): ``fake_iter_seconds`` sleeps per iteration
    so progress and cancel can be observed, ``fake_infosets``/``fake_actions``
    size the strategy, ``fake_fail_solve`` makes ``solve`` raise. Phase 4 failure injection:
    ``fake_build_seconds`` sleeps in ``build``; ``fake_oom_on_build`` /
    ``fake_oom_at_iteration`` raise a CUDA-style ``OutOfMemoryError``;
    ``fake_peak_vram_bytes`` is reported as the worker's peak VRAM. Real solver
    options (``variant``, ``seed``, ...) are accepted and ignored.
    """

    def __init__(self) -> None:
        self._built = False
        self._name = ""
        self._iterations = 0
        self._iter_seconds = 0.0
        self._num_infosets = _KUHN_INFOSETS
        self._num_actions = 2
        self._fail_solve = False
        self._default_iterations = 1000
        self._oom_at_iteration: Optional[int] = None
        self._peak_vram = 0
        self._dtype = "float32"

    def build(self, game_spec: Any, **options: Any) -> "FakeEngine":
        self._spot = None
        if isinstance(game_spec, dict):
            # A spot is validated and resolved like the real worker does; the
            # tree itself is not built (that needs gpu_cfr).
            config = SpotConfig.from_dict(game_spec)
            self._spot = config.resolve().summary()
            self._name = config.game_name
        elif game_spec in ("kuhn", "kuhn_poker", "fake"):
            self._name = "kuhn_poker" if game_spec != "fake" else "fake"
        else:
            raise ValueError("FakeEngine only builds 'kuhn', 'kuhn_poker', 'fake', or a spot, got %r" % (game_spec,))
        self._peak_vram = int(options.get("fake_peak_vram_bytes", 0))
        self._dtype = str(options.get("dtype", "float32"))
        if options.get("fake_build_seconds"):
            time.sleep(float(options["fake_build_seconds"]))
        if options.get("fake_oom_on_build"):
            self._built = False
            raise OutOfMemoryError(
                "CUDA out of memory. Tried to allocate 2.00 GiB (fake_oom_on_build)"
            )
        oom_at = options.get("fake_oom_at_iteration")
        self._oom_at_iteration = None if oom_at is None else int(oom_at)
        self._iter_seconds = float(options.get("fake_iter_seconds", 0.0))
        self._num_infosets = int(options.get("fake_infosets", _KUHN_INFOSETS))
        self._num_actions = int(options.get("fake_actions", 2))
        self._fail_solve = bool(options.get("fake_fail_solve", False))
        self._default_iterations = int(options.get("iterations", 1000))
        if self._num_infosets <= 0 or self._num_actions <= 0:
            raise ValueError("fake_infosets and fake_actions must be positive")
        self._iterations = 0
        self._built = True
        return self

    def estimate_spot(self, spot: Any, dtype: str = "float32") -> Dict[str, Any]:
        """Resolve the spot and report a plausible node count and VRAM estimate."""
        from .memory import estimate_vram_bytes

        config = SpotConfig.from_dict(spot)
        resolved = config.resolve()
        num_nodes = 1 + resolved.num_deals * 15
        return {
            "num_nodes": num_nodes,
            "estimated_vram_bytes": int(estimate_vram_bytes(num_nodes, dtype=dtype)),
            "spot": resolved.summary(),
        }

    def _require_built(self) -> None:
        if not self._built:
            raise RuntimeError("SolverEngine is not built; call build() first")

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def iterations(self) -> int:
        return self._iterations

    @property
    def device(self) -> Optional[str]:
        return "fake" if self._built else None

    @property
    def dtype(self) -> Optional[str]:
        return self._dtype if self._built else None

    @property
    def estimated_vram_bytes(self) -> int:
        return 0

    def describe(self) -> Dict[str, Any]:
        self._require_built()
        return {
            "game": self._name,
            "num_nodes": _KUHN_NODES if self._name == "kuhn_poker" else self._num_infosets * 4,
            "num_infosets": self._num_infosets,
            "num_infoset_actions": self._num_infosets * self._num_actions,
            "estimated_vram_bytes": 0,
            "device": "fake",
            "dtype": self._dtype,
            "spot": self._spot,
        }

    def exploitability(self) -> float:
        self._require_built()
        return 0.5 / math.sqrt(1.0 + self._iterations)

    def _step(self, n: int) -> None:
        if self._iter_seconds > 0:
            time.sleep(self._iter_seconds * n)
        self._iterations += n
        if self._oom_at_iteration is not None and self._iterations >= self._oom_at_iteration:
            raise OutOfMemoryError(
                "CUDA out of memory. Tried to allocate 512.00 MiB (fake_oom_at_iteration)"
            )

    def vram_stats(self) -> Dict[str, int]:
        if not self._peak_vram:
            return {}
        return {"peak_reserved_bytes": self._peak_vram, "peak_allocated_bytes": self._peak_vram}

    def solve(
        self,
        iterations: Optional[int] = None,
        time_budget: Optional[float] = None,
        target_exploitability: Optional[float] = None,
        progress_every: Optional[int] = None,
        progress_callback: Optional[Callable[[FakeProgress], None]] = None,
        chunk_iterations: Optional[int] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> FakeSolveResult:
        self._require_built()
        if self._fail_solve:
            raise RuntimeError("fake solve failure")
        modes = [iterations is not None, time_budget is not None, target_exploitability is not None]
        if sum(modes) > 1:
            raise ValueError("solve accepts only one of iterations, time_budget, target_exploitability")
        if not any(modes):
            iterations = self._default_iterations
        if iterations is not None and int(iterations) <= 0:
            raise ValueError("iterations must be positive")
        if target_exploitability is not None and float(target_exploitability) <= 0:
            raise ValueError("target_exploitability must be positive")

        chunk = int(chunk_iterations or progress_every or 10)
        start = time.perf_counter()
        run = 0
        last_report = 0
        cancelled = False
        target_reached = False

        def elapsed() -> float:
            return time.perf_counter() - start

        while True:
            if iterations is not None and run >= int(iterations):
                break
            if time_budget is not None and elapsed() >= float(time_budget):
                break
            if target_exploitability is not None and self.exploitability() <= float(target_exploitability):
                target_reached = True
                break
            if should_stop is not None and should_stop():
                cancelled = True
                break
            n = chunk if iterations is None else min(chunk, int(iterations) - run)
            self._step(n)
            run += n
            if progress_callback is not None and run - last_report >= int(progress_every or 100):
                last_report = run
                progress_callback(FakeProgress(self._iterations, run, elapsed(), self.exploitability()))

        return FakeSolveResult(
            iterations_run=run,
            total_iterations=self._iterations,
            elapsed_seconds=elapsed(),
            exploitability=self.exploitability(),
            profile_value_p0=-1.0 / 18.0,
            target_exploitability=None if target_exploitability is None else float(target_exploitability),
            target_reached=target_reached,
            time_budget_exhausted=time_budget is not None and elapsed() >= float(time_budget),
            cancelled=cancelled,
        )

    def get_strategy(self) -> Dict[str, Any]:
        self._require_built()
        weight = 1.0 / (1.0 + self._iterations)
        base = np.arange(1, self._num_actions + 1, dtype=np.float32)
        strategy = {}
        keys: List[str] = ["fake/%05d" % index for index in range(self._num_infosets)]
        for index, key in enumerate(keys):
            row = 1.0 + weight * np.roll(base, index)
            strategy[key] = (row / row.sum()).astype(np.float32)
        return strategy

    def strategy_action_names(self) -> Dict[str, Any]:
        self._require_built()
        names = ["a%d" % index for index in range(self._num_actions)]
        return {"fake/%05d" % index: list(names) for index in range(self._num_infosets)}

    def reset(self) -> "FakeEngine":
        self._require_built()
        self._iterations = 0
        return self

    def close(self) -> None:
        self._built = False
        self._iterations = 0
