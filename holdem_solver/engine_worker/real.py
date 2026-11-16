"""The real engine: ``SolverEngine`` plus the metadata the worker reports.

This module is only imported when the worker runs with ``--engine real``.
"""

from typing import Any, Dict

from holdem_solver.engine import SolverEngine


class RealEngine(SolverEngine):
    def describe(self) -> Dict[str, Any]:
        spec = self.game_spec
        if spec is None:
            raise RuntimeError("SolverEngine is not built; call build() first")
        return {
            "game": str(spec.name),
            "num_nodes": len(spec.nodes),
            "num_infosets": len(spec.infosets),
            "num_infoset_actions": sum(len(infoset.actions) for infoset in spec.infosets.values()),
            "estimated_vram_bytes": int(self.estimated_vram_bytes),
            "device": self.device,
        }


def runtime_info() -> Dict[str, Any]:
    """Import torch/gpu_cfr now so a ready worker is a usable worker."""
    from holdem_solver.engine.solver import _load_gpu_cfr

    torch = _load_gpu_cfr().torch
    info: Dict[str, Any] = {
        "torch": str(torch.__version__),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        "cuda_device_name": None,
    }
    if info["cuda_available"]:
        info["cuda_device_name"] = str(torch.cuda.get_device_name(0))
    return info


def vram_stats() -> Dict[str, int]:
    from holdem_solver.engine.solver import _load_gpu_cfr

    torch = _load_gpu_cfr().torch
    if not torch.cuda.is_available():
        return {}
    return {
        "allocated_bytes": int(torch.cuda.memory_allocated()),
        "reserved_bytes": int(torch.cuda.memory_reserved()),
        "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
    }


def reset_peak_vram() -> None:
    from holdem_solver.engine.solver import _load_gpu_cfr

    torch = _load_gpu_cfr().torch
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
