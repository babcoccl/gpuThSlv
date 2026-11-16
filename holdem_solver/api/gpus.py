"""pynvml GPU memory/utilization stats; degrades to None when unavailable."""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger("holdem_solver.api")

try:
    import pynvml

    _NVML = True
except ImportError:
    pynvml = None  # type: ignore[assignment]
    _NVML = False

_initialized = False


def _init() -> bool:
    global _initialized
    if not _NVML or _initialized:
        return _initialized
    try:
        pynvml.nvmlInit()
        _initialized = True
    except Exception:
        LOG.warning("pynvml init failed; GPU stats will omit memory/utilization", exc_info=True)
    return _initialized


def gpu_stats(index: int) -> dict[str, Any] | None:
    """``{memory_used_bytes, memory_total_bytes, utilization_gpu_pct}`` for GPU ``index``."""
    if not _init():
        return None
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return {
            "memory_used_bytes": int(memory.used),
            "memory_total_bytes": int(memory.total),
            "utilization_gpu_pct": int(util.gpu),
            "utilization_memory_pct": int(util.memory),
        }
    except Exception:
        LOG.warning("pynvml stats failed for gpu %s", index, exc_info=True)
        return None
