#!/usr/bin/env python3
"""Runs in ``.venv-engine``.

Verify the engine venv torch build sees both Blackwell GPUs."""

import torch

print("torch_version", torch.__version__)
print("torch_cuda_version", torch.version.cuda)
print("cuda_available", torch.cuda.is_available())
print("device_count", torch.cuda.device_count())
print("arch_list", torch.cuda.get_arch_list())
for i in range(torch.cuda.device_count()):
    torch.cuda.set_device(i)
    a = torch.randn(256, 256, device=f"cuda:{i}", dtype=torch.float32)
    b = torch.randn(256, 256, device=f"cuda:{i}", dtype=torch.float32)
    c = a @ b
    torch.cuda.synchronize(i)
    print(
        "gpu=%d name=%s capability=%s matmul_shape=%s matmul_sum=%.6f"
        % (i, torch.cuda.get_device_name(i), torch.cuda.get_device_capability(i), tuple(c.shape), float(c.sum()))
    )
