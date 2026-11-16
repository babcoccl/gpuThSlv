"""Peak-VRAM prediction for admission, calibrated by measured job peaks.

The static node-count estimate (``engine_worker.memory``) is a lower-level
model of the solver's tensors. Every finished job records its measured
``peak_reserved_bytes`` next to its estimate; once enough comparable samples
exist, the prediction uses the largest recent peak/estimate ratio for that
dtype plus a 5% margin.

Why the seed is 1.6 and needs 3 samples (Phase 5): Phase 4 measured a
153k-node spot peaking at 98 MiB against a 62.7 MiB estimate, a ratio of
1.56, while the Phase 3 static factor was 1.15. A fresh database (or one
dtype with no history) would therefore admit mid-size jobs at ~35% below
their real peak. 1.6 covers the largest ratio measured so far, and the
seed is kept until at least ``MIN_SAMPLES`` finished jobs with an estimate
of at least 64 MiB exist, so one unlucky (low-ratio) sample cannot lower
the admission factor on its own. Below 64 MiB the fixed CUDA allocator
and evaluator overhead dominates the ratio (2 MiB blocks against a few MiB
of tensors), so those samples say nothing about large spots.
"""

from __future__ import annotations

from typing import Any

SEED_FACTOR = 1.6
STATIC_FACTOR = SEED_FACTOR  # Phase 4 name
MEASURED_MARGIN = 1.05
MIN_SAMPLES = 3
# Below this the fixed allocator/evaluator overhead dominates the ratio.
MIN_SAMPLE_ESTIMATE_BYTES = 64 * 1024**2


class VramModel:
    def __init__(
        self,
        store: Any,
        *,
        static_factor: float = SEED_FACTOR,
        margin: float = MEASURED_MARGIN,
        min_samples: int = MIN_SAMPLES,
    ) -> None:
        self._store = store
        self.static_factor = static_factor
        self.margin = margin
        self.min_samples = min_samples
        self.min_sample_bytes = MIN_SAMPLE_ESTIMATE_BYTES

    def factor(self, dtype: str = "float32") -> tuple[float, str, int]:
        """``(factor, source, samples)``: source is ``measured`` once there are
        ``min_samples`` qualifying samples, else ``seed``."""
        samples = self._store.vram_samples(dtype, min_estimate=self.min_sample_bytes)
        if len(samples) < self.min_samples:
            return self.static_factor, "seed", len(samples)
        ratio = max(peak / est for est, peak in samples)
        return max(1.0, ratio * self.margin), "measured", len(samples)

    def predict(self, estimated_bytes: int, dtype: str = "float32") -> int:
        factor, _, _ = self.factor(dtype)
        return int(estimated_bytes * factor)
