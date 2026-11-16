#!/usr/bin/env python3
"""Verify API venv imports and both-GPU torch matmuls."""

import importlib.metadata

import fastapi
import numpy
import pydantic
import pytest
import torch
import uvicorn

for name in ["fastapi", "uvicorn", "pydantic", "numpy", "pytest", "torch"]:
    print("installed", name, importlib.metadata.version(name))
print("fastapi_import", fastapi.__name__)
print("uvicorn_import", uvicorn.__name__)
print("pydantic_import", pydantic.__name__)
print("numpy_import", numpy.__name__)
print("pytest_import", pytest.__name__)

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
