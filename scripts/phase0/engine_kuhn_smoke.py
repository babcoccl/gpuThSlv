#!/usr/bin/env python3
"""Runs in ``.venv-engine``.

Run the README Kuhn API path against the single visible CUDA device.

Run once with CUDA_VISIBLE_DEVICES=0 and once with CUDA_VISIBLE_DEVICES=1.
"""

import os

import torch

from gpu_cfr.config import CFRConfig, DeviceConfig
from gpu_cfr.games.kuhn import build_kuhn_game
from gpu_cfr.solvers.stagewise import StagewiseCFRSolver

print("CUDA_VISIBLE_DEVICES", os.environ.get("CUDA_VISIBLE_DEVICES"))
print("torch_version", torch.__version__)
print("cuda_available", torch.cuda.is_available())
print("device_count", torch.cuda.device_count())
print("device_name", torch.cuda.get_device_name(0))
print("device_capability", torch.cuda.get_device_capability(0))

game = build_kuhn_game()
config = CFRConfig(
    game="kuhn",
    variant="cfr_plus",
    iterations=10,
    device=DeviceConfig(device="cuda", dtype="float32"),
)
solver = StagewiseCFRSolver(game, config)
solver.train()
strategy = solver.average_strategy_for_kuhn_oracle()
strategy_items = list(strategy.items())
print("trained_iterations", solver.iterations)
print("cuda_graph_active", solver.cuda_graph_active)
print("scheduler_name", solver.scheduler_name)
print("num_exec_blocks", solver.num_exec_blocks)
print("strategy_entries", len(strategy_items))
print("first_strategy", strategy_items[0])
