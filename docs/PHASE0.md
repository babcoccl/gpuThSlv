# Phase 0 — environment and viability

Date: 2026-11-16  
Branch: `phase0-env`  
Verdict: **GO-A** — both required runtimes work on this host, and the GPU-CFR engine runs on both RTX 5060 Ti GPUs.

## What was verified

| Area | Result | Evidence |
|---|---|---|
| Host GPU/driver/CUDA | PASS | Driver `595.71.05`, driver-reported CUDA `13.2`, toolkit `nvcc` CUDA `13.2.78`, 2x RTX 5060 Ti, compute capability `12.0` |
| GPU-CFR submodule | PASS | Added read-only at `third_party/GPU-CFR`, commit `4524b727642ffc99055d7c51d9eff11744155c27`; submodule worktree is clean |
| Engine runtime | PASS | `.venv-engine` Python `3.9.25`, torch `2.8.0+cu128`, `torch.cuda.is_available() == True`, device capability `(12, 0)`, matmul on both GPUs |
| Engine smoke | PASS | `gpu_cfr` imports; Kuhn API smoke trains 10 iterations with CUDA graphs active on both `CUDA_VISIBLE_DEVICES=0` and `=1` |
| Engine benchmarks | PASS | `iter_bench` completed all 12 runs: 3 games x {CUDA graph, eager} x 2 GPUs, `--iters 1000 --warmup 50 --reps 3 --variant cfr_plus` |
| API runtime | PASS | `.venv-api` Python `3.15.2`, torch `2.14.1+cu130`, `torch.cuda.device_count() == 2`, matmul on both GPUs |
| API dependencies | PASS | `fastapi`, `uvicorn`, `pydantic`, `numpy`, `pytest` installed from PyPI with no resolver or wheel failures |

## 1. Host facts

Raw log: `scripts/phase0/logs/host_facts.txt`  
Script: `scripts/phase0/host_facts.sh`

| Fact | Value |
|---|---|
| Kernel | `Linux aihub-MS-7C60 7.0.0-38-generic #38~24.04.4-Ubuntu SMP PREEMPT_DYNAMIC Mon Sep 14 16:37:11 UTC 2 x86_64 x86_64 x86_64 GNU/Linux` |
| NVIDIA driver | `595.71.05` |
| Driver-reported CUDA | `13.2` |
| CUDA toolkit | `/usr/local/cuda/bin/nvcc`, release `13.2`, `V13.2.78` |
| GPU 0 | `NVIDIA GeForce RTX 5060 Ti`, `16311 MiB`, compute capability `12.0` |
| GPU 1 | `NVIDIA GeForce RTX 5060 Ti`, `16311 MiB`, compute capability `12.0` |
| System Python | `Python 3.12.3` at `/usr/bin/python3` |
| API Python | `Python 3.15.2` at `/usr/bin/python3.15` |
| Engine Python | `Python 3.9.25` installed by `uv` at `/home/aihub/.local/bin/python3.9` |
| uv | `uv 0.11.21 (x86_64-unknown-linux-gnu)` |

Key raw output:

```text
$ nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,compute_cap --format=csv
index, name, driver_version, memory.total [MiB], memory.used [MiB], compute_cap
0, NVIDIA GeForce RTX 5060 Ti, 595.71.05, 16311 MiB, 378 MiB, 12.0
1, NVIDIA GeForce RTX 5060 Ti, 595.71.05, 16311 MiB, 20 MiB, 12.0

$ nvcc --version
Cuda compilation tools, release 13.2, V13.2.78
Build cuda_13.2.r13.2/compiler.37668154_0

$ python3.15 --version
Python 3.15.2
$ uv --version
uv 0.11.21 (x86_64-unknown-linux-gnu)
```

The NVIDIA driver and CUDA toolkit are both present, so Phase 0 proceeded.

## 2. GPU-CFR submodule

Command:

```bash
git submodule add https://github.com/lbn187/GPU-CFR third_party/GPU-CFR
```

Result:

```text
Cloning into '/home/aihub/repos/gpuThSlv/third_party/GPU-CFR'...
 4524b727642ffc99055d7c51d9eff11744155c27 third_party/GPU-CFR (heads/main)
```

`.gitmodules` records the upstream URL:

```ini
[submodule "third_party/GPU-CFR"]
	path = third_party/GPU-CFR
	url = https://github.com/lbn187/GPU-CFR
```

The submodule was not modified. `git -C third_party/GPU-CFR status --short` is empty. All engine commands after the first import use `PYTHONDONTWRITEBYTECODE=1` to avoid refreshing tracked `.pyc` files inside the submodule.

Inventory raw log: `scripts/phase0/logs/submodule_inventory.txt`  
Script: `scripts/phase0/inventory_submodule.sh`

| Inventory item | Result |
|---|---:|
| Tracked files | 114 |
| `.so` files under `gpu_cfr/` + `probes/` | 69 |
| `.so` files under `gpu_cfr/` only | 58 |
| `.so` files under `probes/` | 11 |
| Non-`cpython-39-x86_64-linux-gnu` `.so` files | 0 |

License status is ambiguous and should be resolved before any redistribution or commercial use:

- No `LICENSE`, `LICENCE`, `COPYING`, or `NOTICE` file is tracked.
- `README.md` has a badge claiming `CC BY-NC-SA 4.0`.
- GitHub API reports `license: null`.

Raw GitHub license API output:

```json
{
  "full_name": "lbn187/GPU-CFR",
  "license": null,
  "private": false,
  "default_branch": "main"
}
```

`BUILD_INFO.txt` states the extensions were built for CPython `3.9.16`, ABI tag `cpython-39`, platform `Linux x86_64`, with torch `1.13.1+cu117` at build time.

### Exact `.so` filenames

All 69 shipped extension filenames end in `cpython-39-x86_64-linux-gnu.so`.

<details>
<summary>69 `.so` filenames</summary>

```text
gpu_cfr/benchmarks/artifacts.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/efg_export_cache.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/hunl_river_openspiel_cfr.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/hunl_river_openspiel.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/hunl_river_openspiel_driver_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/liteefg.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/noregret.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/baselines/openspiel.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/cli_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/curve_cli_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/gates.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/iter_bench_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/kernel_bench_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/lifecycle_bench_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/matrix_evidence.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/payoff_microbench_cli_impl.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/payoff_microbench.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/payoff_microbench_report.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/profiler.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/registry.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/runners.cpython-39-x86_64-linux-gnu.so
gpu_cfr/benchmarks/schema.cpython-39-x86_64-linux-gnu.so
gpu_cfr/config.cpython-39-x86_64-linux-gnu.so
gpu_cfr/devices.cpython-39-x86_64-linux-gnu.so
gpu_cfr/efg/compiler.cpython-39-x86_64-linux-gnu.so
gpu_cfr/efg/efg_file.cpython-39-x86_64-linux-gnu.so
gpu_cfr/efg/graph.cpython-39-x86_64-linux-gnu.so
gpu_cfr/efg/spec.cpython-39-x86_64-linux-gnu.so
gpu_cfr/efg/stages.cpython-39-x86_64-linux-gnu.so
gpu_cfr/efg/validation.cpython-39-x86_64-linux-gnu.so
gpu_cfr/eval/best_response.cpython-39-x86_64-linux-gnu.so
gpu_cfr/eval/br_oracles.cpython-39-x86_64-linux-gnu.so
gpu_cfr/eval/exploitability.cpython-39-x86_64-linux-gnu.so
gpu_cfr/eval/metrics.cpython-39-x86_64-linux-gnu.so
gpu_cfr/eval/seed_invariance.cpython-39-x86_64-linux-gnu.so
gpu_cfr/eval/strategy_export.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/hunl_cards.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/hunl_libratus.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/hunl_poker_eval.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/hunl_river.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/hunl_turn.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/kuhn.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/openspiel_adapter.cpython-39-x86_64-linux-gnu.so
gpu_cfr/games/registry.cpython-39-x86_64-linux-gnu.so
gpu_cfr/logging_utils.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/base.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/gpu_cfr.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/lifecycle.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/scheduler.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/snapshot_reference.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/stagewise.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/update_rules.cpython-39-x86_64-linux-gnu.so
gpu_cfr/solvers/vectorized_kuhn.cpython-39-x86_64-linux-gnu.so
gpu_cfr/tensors/aggregation.cpython-39-x86_64-linux-gnu.so
gpu_cfr/tensors/edges.cpython-39-x86_64-linux-gnu.so
gpu_cfr/tensors/layout.cpython-39-x86_64-linux-gnu.so
gpu_cfr/tensors/memory.cpython-39-x86_64-linux-gnu.so
gpu_cfr/tensors/sparse_payoff.cpython-39-x86_64-linux-gnu.so
probes/overhead_probe_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_alternating_gap_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_eval_precision_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_libratus_fullrange_solve_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_libratus_our_feasibility_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_libratus_range_ceiling_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_pcfr_alt_factorial_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_sparse_ops_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_variant_order_determinism_impl.cpython-39-x86_64-linux-gnu.so
probes/probe_variant_order_race_impl.cpython-39-x86_64-linux-gnu.so
probes/scaling_sweep_impl.cpython-39-x86_64-linux-gnu.so
```

</details>

## 3. Engine venv

Commands:

```bash
uv python install 3.9
uv venv .venv-engine --python 3.9 --seed
uv pip install --python .venv-engine/bin/python torch numpy
```

Torch candidate record:

- PyPI's latest torch at check time was `2.15.0`; its release files included no `cp39` `x86_64` wheel, so it was not installable on Python 3.9. Raw check output:

```text
PyPI latest: 2.15.0
cp39 files for latest:
```

- The engine install attempt was `uv pip install --python .venv-engine/bin/python torch numpy`. The resolver selected `torch==2.8.0` from the PyPI `torch-2.8.0-cp39-cp39-manylinux_2_28_x86_64.whl` wheel and installed CUDA 12.8 dependency wheels. Runtime reports `2.8.0+cu128`.
- No torch install attempt failed, so the cu126/cu128/cu130 fallback builds were not needed.

Install output excerpt:

```text
Using Python 3.9.25 environment at: .venv-engine
Resolved 28 packages in 345ms
Installed 27 packages in 291ms
 + numpy==2.0.2
 + torch==2.8.0
 + triton==3.4.0
 ...
```

Exact torch check command requested:

```bash
.venv-engine/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_capability(0))"
```

Output:

```text
True (12, 0)
```

Both-GPU check output from `scripts/phase0/logs/engine_checks.txt`:

```text
=== exact torch check ===
True (12, 0)

=== engine torch check (all visible devices) ===
torch_version 2.8.0+cu128
torch_cuda_version 12.8
cuda_available True
device_count 2
arch_list ['sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']
gpu=0 name=NVIDIA GeForce RTX 5060 Ti capability=(12, 0) matmul_shape=(256, 256) matmul_sum=-571.302979
gpu=1 name=NVIDIA GeForce RTX 5060 Ti capability=(12, 0) matmul_shape=(256, 256) matmul_sum=-1393.418457
```

`arch_list` includes `sm_120`, confirming Blackwell support.

### Public API capture

Script: `scripts/phase0/capture_gpu_cfr_api.sh`  
Raw help log: `scripts/phase0/logs/gpu_cfr_api_help.txt` (1602 lines)

Key public API signatures observed via `help()`:

```text
from gpu_cfr.games.kuhn import build_kuhn_game
from gpu_cfr.config import CFRConfig, DeviceConfig
from gpu_cfr.solvers.stagewise import StagewiseCFRSolver

build_kuhn_game() -> 'GameSpec'
CFRConfig(game: 'str' = 'kuhn', variant: "Literal['cfr', 'cfr_plus', 'dcfr', 'pcfr_plus', 'hs_dcfr']" = 'cfr_plus', iterations: 'int' = 1000, ..., cuda_graph: 'bool' = True, cuda_graph_unroll: 'int' = 1)
StagewiseCFRSolver(game: 'GameSpec', config: 'CFRConfig | None' = None, scheduler: 'CFRScheduler | None' = None)
StagewiseCFRSolver.train(self) -> 'None'
StagewiseCFRSolver.step(self, num_iterations: 'int' = 1) -> 'None'
StagewiseCFRSolver.average_strategy_for_kuhn_oracle(self) -> 'Dict[tuple[int, tuple[str, ...]], np.ndarray]'
```

One caveat found while capturing help: `gpu_cfr.eval.exploitability` imports `project_llm_opp_prior` and raises `ImportError` if that sibling package is not on `PYTHONPATH`. This did not affect Kuhn/HUNL benchmarks.

### Kuhn smoke test

The vendored README’s Kuhn section is an import-only API snippet (`README.md` lines 172-178); it does not contain an executable solve example. I ran the smallest solve path enabled by that snippet: build the Kuhn `GameSpec`, construct `CFRConfig`/`DeviceConfig` for CUDA, instantiate `StagewiseCFRSolver`, train 10 iterations, and export the Kuhn-oracle average strategy.

Script: `scripts/phase0/engine_kuhn_smoke.py`  
Runner: `scripts/phase0/run_engine_checks.sh`  
Raw log: `scripts/phase0/logs/engine_checks.txt`

Commands:

```bash
CUDA_VISIBLE_DEVICES=0 PYTHONPATH=third_party/GPU-CFR PYTHONDONTWRITEBYTECODE=1 .venv-engine/bin/python scripts/phase0/engine_kuhn_smoke.py
CUDA_VISIBLE_DEVICES=1 PYTHONPATH=third_party/GPU-CFR PYTHONDONTWRITEBYTECODE=1 .venv-engine/bin/python scripts/phase0/engine_kuhn_smoke.py
```

Both outputs were identical except for the visible-device label:

```text
CUDA_VISIBLE_DEVICES 0
cuda_available True
device_count 1
device_name NVIDIA GeForce RTX 5060 Ti
device_capability (12, 0)
trained_iterations 10
cuda_graph_active True
scheduler_name stagewise
num_exec_blocks 4
strategy_entries 12
first_strategy ((0, ()), array([0.7871065, 0.2128935]))
```

GPU 1 produced the same values with `CUDA_VISIBLE_DEVICES 1`.

## 4. API venv

Commands:

```bash
uv venv .venv-api --python 3.15.2 --seed
uv pip install --python .venv-api/bin/python --index-url https://download.pytorch.org/whl/cu130 torch
uv pip install --python .venv-api/bin/python fastapi uvicorn pydantic numpy pytest
```

Torch resolution used the cu130 index as required for cp315:

```text
Resolved 26 packages in 152ms
Installed 26 packages in 245ms
 + torch==2.14.1+cu130
 + cuda-toolkit==13.0.3.0
 ...
```

API dependency install:

```text
Resolved 20 packages in 513ms
Installed 19 packages in 29ms
 + fastapi==0.163.1
 + uvicorn==0.54.0
 + pydantic==2.14.3
 + numpy==2.5.5
 + pytest==9.2.0
 ...
```

No resolver or wheel failures occurred. The first torch-only check emitted a NumPy warning because NumPy was not yet installed; the subsequent dependency install added `numpy==2.5.5`.

Script: `scripts/phase0/api_check.py`  
Raw log: `scripts/phase0/logs/api_check.txt`

```text
installed fastapi 0.163.1
installed uvicorn 0.54.0
installed pydantic 2.14.3
installed numpy 2.5.5
installed pytest 9.2.0
installed torch 2.14.1+cu130
torch_version 2.14.1+cu130
torch_cuda_version 13.0
cuda_available True
device_count 2
arch_list ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']
gpu=0 name=NVIDIA GeForce RTX 5060 Ti capability=(12, 0) matmul_shape=(256, 256) matmul_sum=7300.807617
gpu=1 name=NVIDIA GeForce RTX 5060 Ti capability=(12, 0) matmul_shape=(256, 256) matmul_sum=4484.682617
```

Package lists are frozen in `scripts/phase0/logs/engine_pip_list.txt` and `scripts/phase0/logs/api_pip_list.txt`.

## 5. Benchmarks

Runner: `scripts/phase0/run_iter_bench_matrix.sh`  
Wrapper: `scripts/phase0/run_iter_bench_with_memory.py`  
Summarizer: `scripts/phase0/summarize_benchmarks.py`

Configuration for every run:

```text
game: kuhn_poker | hunl_river | hunl_turn
device: cuda
variant: cfr_plus
iters: 1000
warmup: 50
reps: 3
dtype: float32
CUDA graph mode: --cuda-graph and --no-cuda-graph
GPU pinning: CUDA_VISIBLE_DEVICES=0 and CUDA_VISIBLE_DEVICES=1
OMP_NUM_THREADS=8
PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=third_party/GPU-CFR
```

`iter_bench` reports `ms_per_iter` but not VRAM. The wrapper delegates to the unchanged `gpu_cfr.benchmarks.iter_bench_impl.main()` and then prints one JSON object containing `torch.cuda.max_memory_allocated()` and `torch.cuda.max_memory_reserved()` for the whole process. All 12 worker runs exited `0`.

Raw logs:

- `scripts/phase0/logs/iter_bench_gpu0.jsonl`
- `scripts/phase0/logs/iter_bench_gpu1.jsonl`
- `scripts/phase0/logs/iter_bench_gpu0.stderr.log`
- `scripts/phase0/logs/iter_bench_gpu1.stderr.log`
- `scripts/phase0/logs/iter_bench_status_gpu0.tsv`
- `scripts/phase0/logs/iter_bench_status_gpu1.tsv`
- `scripts/phase0/logs/iter_bench_matrix.log`

Matrix completion line:

```text
matrix_done gpu0_worker_status=0 gpu1_worker_status=0 iters=1000 warmup=50 reps=3
```

Representative raw `iter_bench` JSONL lines from GPU 0 (`kuhn_poker`, CUDA graph, rep 1, followed by its memory probe):

```json
{"game": "kuhn_poker", "device": "cuda", "dtype": "float32", "variant": "cfr_plus", "rep": 1, "alternating_updates": false, "traversals_per_iter": 1, "iters": 1000, "warmup": 50, "num_nodes": 55, "num_exec_blocks": 4, "cuda_graph_active": true, "cuda_graph_unroll": 1, "torch_threads": 8, "spec_sec": 0.00024289799966936698, "build_sec": 0.4707762379998712, "load_1min": 1.4208984375, "elapsed_ms": 64.37904357910156, "ms_per_iter": 0.06437904357910157, "iters_per_sec": 15533.004909762521}
{"memory_probe": {"argv": ["--game", "kuhn_poker", "--device", "cuda", "--variant", "cfr_plus", "--iters", "1000", "--warmup", "50", "--reps", "3", "--cuda-graph"], "device_count": 1, "peak_memory_allocated_bytes": 35840, "peak_memory_reserved_bytes": 8388608}}
```

### Results

Median is across the 3 reps; range is min-max. Peak VRAM is process peak allocated/reserved from `torch.cuda.max_memory_allocated()` / `max_memory_reserved()`.

| GPU | Game | Mode | Median ms/iter | Min–max ms/iter | Peak allocated MiB | Peak reserved MiB | Nodes |
|---:|---|---|---:|---:|---:|---:|---:|
| 0 | kuhn_poker | CUDA graph | 0.064379 | 0.064319–0.064674 | 0.034 | 8.000 | 55 |
| 0 | kuhn_poker | eager | 0.561186 | 0.532958–0.561569 | 0.033 | 2.000 | 55 |
| 0 | hunl_river | CUDA graph | 0.127336 | 0.126897–0.129987 | 33.921 | 54.000 | 137416 |
| 0 | hunl_river | eager | 0.525693 | 0.522588–0.546473 | 33.921 | 48.000 | 137416 |
| 0 | hunl_turn | CUDA graph | 0.354721 | 0.354333–0.360709 | 108.299 | 142.000 | 434266 |
| 0 | hunl_turn | eager | 0.814052 | 0.808536–0.838887 | 108.299 | 136.000 | 434266 |
| 1 | kuhn_poker | CUDA graph | 0.061506 | 0.061504–0.061518 | 0.034 | 8.000 | 55 |
| 1 | kuhn_poker | eager | 0.511429 | 0.510159–0.516746 | 0.033 | 2.000 | 55 |
| 1 | hunl_river | CUDA graph | 0.120926 | 0.120926–0.120982 | 33.921 | 54.000 | 137416 |
| 1 | hunl_river | eager | 0.534050 | 0.532788–0.538274 | 33.921 | 48.000 | 137416 |
| 1 | hunl_turn | CUDA graph | 0.334298 | 0.334257–0.334560 | 108.299 | 142.000 | 434266 |
| 1 | hunl_turn | eager | 0.813932 | 0.812355–0.825353 | 108.299 | 136.000 | 434266 |

Summary artifacts:

- `scripts/phase0/logs/benchmark_summary.json`
- `scripts/phase0/logs/benchmark_summary.csv`
- `scripts/phase0/logs/benchmark_summary.txt`

## 6. Failures, caveats, and notes

- No Phase 0 step failed. Host, submodule, engine, API, and benchmark steps all completed.
- The engine torch build was found on the first candidate tried: PyPI `torch` for cp39 resolved to torch 2.8.0 with CUDA 12.8 wheels and `sm_120` in `torch.cuda.get_arch_list()`. No fallback torch build was needed.
- `gpu_cfr.eval.exploitability` requires a sibling `project_llm_opp_prior` package that is not vendored; this is only relevant if Phase 1+ uses that oracle path.
- GPU-CFR license status is ambiguous: README claims CC BY-NC-SA 4.0, but there is no license file and GitHub reports `license: null`.
- GPU 0 has desktop/Xorg processes using some VRAM, but all benchmarks still completed with peak allocated VRAM far below 16 GiB.
- `iter_bench` does not emit VRAM metrics; the committed wrapper adds `torch.cuda.max_memory_allocated()`/`max_memory_reserved()` without changing the benchmark implementation.

## 7. Phase 0 file map

Scripts:

- `scripts/phase0/host_facts.sh`
- `scripts/phase0/inventory_submodule.sh`
- `scripts/phase0/capture_gpu_cfr_api.sh`
- `scripts/phase0/engine_torch_check.py`
- `scripts/phase0/engine_kuhn_smoke.py`
- `scripts/phase0/run_engine_checks.sh`
- `scripts/phase0/api_check.py`
- `scripts/phase0/run_iter_bench_with_memory.py`
- `scripts/phase0/run_iter_bench_matrix.sh`
- `scripts/phase0/summarize_benchmarks.py`

Logs and summaries:

- `scripts/phase0/logs/host_facts.txt`
- `scripts/phase0/logs/submodule_inventory.txt`
- `scripts/phase0/logs/gpu_cfr_api_help.txt`
- `scripts/phase0/logs/engine_checks.txt`
- `scripts/phase0/logs/api_check.txt`
- `scripts/phase0/logs/engine_pip_list.txt`
- `scripts/phase0/logs/api_pip_list.txt`
- `scripts/phase0/logs/iter_bench_gpu0.jsonl`
- `scripts/phase0/logs/iter_bench_gpu1.jsonl`
- `scripts/phase0/logs/iter_bench_gpu0.stderr.log`
- `scripts/phase0/logs/iter_bench_gpu1.stderr.log`
- `scripts/phase0/logs/iter_bench_status_gpu0.tsv`
- `scripts/phase0/logs/iter_bench_status_gpu1.tsv`
- `scripts/phase0/logs/iter_bench_matrix.log`
- `scripts/phase0/logs/benchmark_summary.json`
- `scripts/phase0/logs/benchmark_summary.csv`
- `scripts/phase0/logs/benchmark_summary.txt`
