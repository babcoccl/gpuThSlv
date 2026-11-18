#!/usr/bin/env python3
"""Runs in ``.venv-engine``.

Run gpu_cfr.benchmarks.iter_bench and append a peak-VRAM JSON line.

The upstream iter_bench driver prints steady-state timing JSON but does not
report torch.cuda.max_memory_allocated. This wrapper resets PyTorch's peak
counters once before delegating to the unchanged iter_bench implementation,
then prints one additional JSON object after it returns or exits.
"""

import json
import sys

import torch

import gpu_cfr.benchmarks.iter_bench_impl as iter_bench


def main():
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    try:
        iter_bench.main()
    finally:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            payload = {
                "memory_probe": {
                    "argv": sys.argv[1:],
                    "peak_memory_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "peak_memory_reserved_bytes": torch.cuda.max_memory_reserved(),
                    "device_count": torch.cuda.device_count(),
                }
            }
            print(json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
