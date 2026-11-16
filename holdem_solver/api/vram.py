"""Peak-VRAM prediction for admission, calibrated by measured job peaks.

The static node-count estimate (``engine_worker.memory``) runs 4-11% under
the measured peak on large spots (Phase 2), so Phase 3 multiplied it by a
fixed 1.15. Phase 4 learns the factor instead: every finished job records its
measured ``peak_reserved_bytes`` next to its estimate, and the prediction uses
the largest recent peak/estimate ratio (plus a margin) for that dtype. Until
there are samples, the static 1.15 is used.
"""

from __future__ import annotations

from typing import Any

STATIC_FACTOR = 1.15
MEASURED_MARGIN = 1.05
# Below this the fixed allocator/evaluator overhead dominates the ratio.
MIN_SAMPLE_ESTIMATE_BYTES = 64 * 1024**2


class VramModel:
    def __init__(self, store: Any, *, static_factor: float = STATIC_FACTOR, margin: float = MEASURED_MARGIN) -> None:
        self._store = store
        self.static_factor = static_factor
        self.margin = margin
        self.min_sample_bytes = MIN_SAMPLE_ESTIMATE_BYTES

    def factor(self, dtype: str = "float32") -> tuple[float, str, int]:
        """``(factor, source, samples)`` where source is ``measured`` or ``static``."""
        samples = self._store.vram_samples(dtype, min_estimate=self.min_sample_bytes)
        if not samples:
            return self.static_factor, "static", 0
        ratio = max(peak / est for est, peak in samples)
        return max(1.0, ratio * self.margin), "measured", len(samples)

    def predict(self, estimated_bytes: int, dtype: str = "float32") -> int:
        factor, _, _ = self.factor(dtype)
        return int(estimated_bytes * factor)
