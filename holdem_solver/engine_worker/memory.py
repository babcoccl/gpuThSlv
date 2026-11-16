"""VRAM estimation helpers for GPU-CFR trees."""

from __future__ import annotations

MIB = 1024 * 1024

# Coefficients are rounded from the Phase 0 RTX 5060 Ti measurements in
# docs/PHASE0.md (Kuhn, HUNL river, and HUNL turn; float32). They are planning
# estimates, not allocator guarantees.
_ALLOCATED_BYTES_PER_NODE = 260.0
_ALLOCATED_BYTES_PER_INFOSSET_ACTION = 35.0
_ALLOCATED_FIXED_BYTES = 32 * 1024
_RESERVED_BYTES_PER_NODE = 322.0
_RESERVED_FIXED_CUDA_GRAPH_BYTES = 10 * MIB
_RESERVED_FIXED_EAGER_BYTES = 4 * MIB


def estimate_vram_bytes(
    num_nodes: int,
    num_infoset_actions: int = 0,
    *,
    dtype: str = "float32",
    cuda_graph: bool = True,
    safety_factor: float = 1.1,
) -> int:
    """Estimate peak reserved VRAM for a compiled tree.

    The estimate is calibrated for float32 CUDA solves on the Phase 0 host and
    is intentionally conservative. ``float64`` scales the estimate by two
    because the solver's float payload doubles; integer edge tables do not,
    so this over-estimates rather than under-estimates. ``cuda_graph=False``
    removes the fixed CUDA-graph pool overhead observed in Phase 0.
    """
    if num_nodes < 0:
        raise ValueError("num_nodes must be non-negative")
    if num_infoset_actions < 0:
        raise ValueError("num_infoset_actions must be non-negative")
    if safety_factor <= 0:
        raise ValueError("safety_factor must be positive")

    dtype_name = str(dtype).lower()
    if dtype_name == "float64":
        dtype_scale = 2.0
    elif dtype_name == "float32":
        dtype_scale = 1.0
    else:
        raise ValueError("dtype must be 'float32' or 'float64'")

    allocated = (
        _ALLOCATED_BYTES_PER_NODE * num_nodes
        + _ALLOCATED_BYTES_PER_INFOSSET_ACTION * num_infoset_actions
        + _ALLOCATED_FIXED_BYTES
    ) * dtype_scale
    reserved = (
        _RESERVED_BYTES_PER_NODE * num_nodes
        + (_RESERVED_FIXED_CUDA_GRAPH_BYTES if cuda_graph else _RESERVED_FIXED_EAGER_BYTES)
        + _ALLOCATED_BYTES_PER_INFOSSET_ACTION * num_infoset_actions
    ) * dtype_scale

    return int(max(allocated, reserved) * safety_factor)
