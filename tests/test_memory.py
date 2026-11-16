import pytest

from holdem_solver.engine_worker import estimate_vram_bytes

MIB = 1024 * 1024


def test_estimate_vram_bytes_is_monotonic_in_tree_size():
    small = estimate_vram_bytes(55, 24)
    river = estimate_vram_bytes(137416, 1400)
    turn = estimate_vram_bytes(434266, 38808)
    assert small < river < turn


def test_estimate_vram_bytes_tracks_phase0_scale():
    # Phase 0 measured 33.921 MiB allocated / 54 MiB reserved for HUNL river.
    river = estimate_vram_bytes(137416, 1400, cuda_graph=True)
    assert 40 * MIB < river < 80 * MIB


def test_estimate_vram_bytes_options():
    base = estimate_vram_bytes(10000, 100, dtype="float32", cuda_graph=True)
    assert estimate_vram_bytes(10000, 100, dtype="float64", cuda_graph=True) > base
    assert estimate_vram_bytes(10000, 100, dtype="float32", cuda_graph=False) < base
    assert estimate_vram_bytes(10000, 100, dtype="float32", cuda_graph=True, safety_factor=2.0) > base


def test_estimate_vram_bytes_validation():
    with pytest.raises(ValueError):
        estimate_vram_bytes(-1)
    with pytest.raises(ValueError):
        estimate_vram_bytes(1, -1)
    with pytest.raises(ValueError):
        estimate_vram_bytes(1, dtype="float16")
    with pytest.raises(ValueError):
        estimate_vram_bytes(1, safety_factor=0)
