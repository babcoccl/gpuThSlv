# Benchmarks (Phase 5)

Fresh runs on 2026-11-16, one process per GPU, both GPUs in parallel:

```bash
.venv-api/bin/python scripts/phase5/bench.py --gpu 0 > scripts/phase5/logs/bench_gpu0.out &
.venv-api/bin/python scripts/phase5/bench.py --gpu 1 > scripts/phase5/logs/bench_gpu1.out &
```

Raw results: `scripts/phase5/logs/bench_gpu{0,1}.jsonl` (one line per spot × dtype).
Every number below is copied from those files by a script; nothing is estimated.

**Hardware / software.** 2× NVIDIA GeForce RTX 5060 Ti 16 GB (Blackwell,
cc 12.0), driver 595.71.05, Ubuntu 24.04.4, kernel 7.0.0-38. Engine worker:
Python 3.9.25, torch 2.8.0+cu128, GPU-CFR compiled modules (cpython-39).
Client: Python 3.15.2. Solver `cfr_plus`, board `KsJsTh7d2c`, pot 20, stack 100.

**Method** (`scripts/phase5/bench.py`). A fresh worker per (spot, dtype), so
the peak VRAM is that spot's alone.

- *Build*: worker `build` (tree + solver allocation), seconds.
- *it/s*: after a 50-iteration warm-up (the first solve pays one-time CUDA
  setup), a timed solve with no exploitability evals.
- *Time to 2e-3 / 1e-3*: after `reset()`, a target solve to 9e-4 with one
  exploitability eval every *eval every* iterations; the time (and
  iteration) of the first eval at or below the threshold, **eval time included**
  — so this is what an API user waiting on `target_exploitability` sees.
  Exploitability is in the API's units (same number as `/v1/solves/{id}`).
- *Peak VRAM*: PyTorch's peak reserved memory in the worker over build +
  both solves. *Est.* is the API's pre-build estimate.

## Spots

| spot | range (both players) | bet sizes | nodes | infosets | eval every |
| --- | --- | --- | ---: | ---: | ---: |
| xs-narrow-pot | QQ+, AKs | pot | 1,999 | 72 | 25 |
| s-mid-standard | TT+, AQs+, AKo, KQs | standard | 17,731 | 240 | 25 |
| m-wide-standard | 77+, A9s+, KTs+, QJs, ATo+, KQo | standard | 152,851 | 672 | 50 |
| l-broad-standard | 22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KTo+, QJo | standard | 573,031 | 1,254 | 50 |
| xl-full-pot | `*` (all 1081 combos) | pot | 9,631,711 | 4,324 | 100 |

## Build time (s)

| spot | GPU0 f32 | GPU0 f64 | GPU1 f32 | GPU1 f64 |
| --- | ---: | ---: | ---: | ---: |
| xs-narrow-pot | 0.51 | 0.41 | 0.51 | 0.41 |
| s-mid-standard | 0.55 | 0.53 | 0.55 | 0.61 |
| m-wide-standard | 1.76 | 1.98 | 1.71 | 1.74 |
| l-broad-standard | 5.21 | 5.84 | 5.90 | 5.42 |
| xl-full-pot | 95.80 | 96.01 | 98.93 | 85.91 |

## Iterations / second

| spot | GPU0 f32 | GPU0 f64 | GPU1 f32 | GPU1 f64 |
| --- | ---: | ---: | ---: | ---: |
| xs-narrow-pot | 10,232 | 9,859 | 10,947 | 10,308 |
| s-mid-standard | 9,932 | 9,340 | 10,492 | 9,673 |
| m-wide-standard | 7,354 | 6,222 | 7,690 | 6,368 |
| l-broad-standard | 2,518 | 1,829 | 2,551 | 1,845 |
| xl-full-pot | 83 | 56 | 83 | 56 |

## Peak VRAM, reserved (MiB) — estimate in parentheses

| spot | GPU0 f32 | GPU0 f64 | GPU1 f32 | GPU1 f64 |
| --- | ---: | ---: | ---: | ---: |
| xs-narrow-pot | 8 (12) | 8 (23) | 8 (12) | 8 (23) |
| s-mid-standard | 12 (17) | 14 (34) | 12 (17) | 14 (34) |
| m-wide-standard | 98 (63) | 114 (125) | 98 (63) | 114 (125) |
| l-broad-standard | 228 (205) | 288 (409) | 228 (205) | 288 (409) |
| xl-full-pot | 3388 (3265) | 4128 (6530) | 3388 (3265) | 4128 (6530) |

## Time to 2e-03 exploitability: seconds (iteration)

| spot | GPU0 f32 | GPU0 f64 | GPU1 f32 | GPU1 f64 |
| --- | ---: | ---: | ---: | ---: |
| xs-narrow-pot | 6.03 (28,800) | 6.11 (28,500) | 5.90 (29,050) | 5.99 (28,675) |
| s-mid-standard | 3.20 (14,975) | 3.33 (14,975) | 3.10 (14,875) | 3.24 (14,975) |
| m-wide-standard | 0.38 (1,950) | 0.42 (1,950) | 0.36 (1,950) | 0.42 (1,950) |
| l-broad-standard | 0.90 (1,950) | 1.18 (1,950) | 0.93 (2,000) | 1.17 (1,950) |
| xl-full-pot | 23.19 (1,900) | 43.67 (2,400) | 23.15 (1,900) | 43.62 (2,400) |

## Time to 1e-03 exploitability: seconds (iteration)

| spot | GPU0 f32 | GPU0 f64 | GPU1 f32 | GPU1 f64 |
| --- | ---: | ---: | ---: | ---: |
| xs-narrow-pot | 8.68 (41,425) | 8.77 (40,925) | 8.44 (41,575) | 8.58 (41,000) |
| s-mid-standard | 6.42 (30,000) | 5.78 (25,900) | 6.02 (28,875) | 5.61 (25,900) |
| m-wide-standard | 0.61 (3,150) | 0.68 (3,150) | 0.59 (3,150) | 0.67 (3,150) |
| l-broad-standard | 1.40 (3,050) | 1.82 (3,000) | 1.38 (3,000) | 1.83 (3,050) |
| xl-full-pot | 37.82 (3,100) | 63.68 (3,500) | 37.76 (3,100) | 63.60 (3,500) |

## float32 vs float64

Mean over both GPUs of float64 ÷ float32:

| spot | it/s | peak VRAM | time to 1e-3 |
| --- | ---: | ---: | ---: |
| xs-narrow-pot | 0.95 | 1.00 | 1.01 |
| s-mid-standard | 0.93 | 1.17 | 0.92 |
| m-wide-standard | 0.84 | 1.16 | 1.13 |
| l-broad-standard | 0.72 | 1.26 | 1.31 |
| xl-full-pot | 0.67 | 1.22 | 1.68 |

## Observations

- The two GPUs agree within a few percent; they are interchangeable.
- Small spots are fast per iteration but need many iterations (~40k for
  `xs`, ~30k for `s`) to reach 1e-3, so they take longer than the 150k–570k
  node spots, which converge in ~3k iterations. Part of the small-spot time
  is the eval every 25 iterations.
- float64 costs little until the tree is large: 0.95× it/s for `xs`, 0.67×
  for the 9.6M-node `xl`, which then takes 1.7× as long to reach 1e-3.
- float64 peaks are well below the estimate (the estimate doubles every
  buffer; the engine does not), while float32 `m-wide` peaks at 1.56× its
  estimate — the ratio that motivates the 1.6 VRAM seed (README, "REST API").
- `xl-full-pot` build (~90 s) dominates a full-range river solve to 1e-3
  (~38 s float32).
- An earlier pass of this script measured it/s without the warm-up solve
  (so it included one-time CUDA setup) and was discarded; only the warm
  numbers are reported.
