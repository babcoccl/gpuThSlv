Project: GPU-accelerated Texas Hold'em CFR solver exposed via REST API (Linux).
Core engine: https://github.com/lbn187/GPU-CFR (fork it into our org; vendor as a git submodule at third_party/GPU-CFR).
Critical facts:
- The engine ships only COMPILED .so modules (CPython 3.9 x86_64-linux ABI). No source. Learn the API via help(), README, and tests. Never try to edit or decompile it.
- Python versions: two runtimes, never mixed.
- API/orchestrator: Python 3.15.2 (.venv-api). Torch only if needed, installed from https://download.pytorch.org/whl/cu130 (cp315 wheels are not on PyPI). Pin and verify triton/other deps resolve; report any metadata or Requires-Python conflicts.
- Engine: Python 3.9 (.venv-engine, via uv python install 3.9) because GPU-CFR ships only cpython-39 .so files. Only holdem_solver/engine_worker/ may import gpu_cfr.
- Communication between them: JSON-RPC over a Unix socket, bulk arrays via .npy files in /dev/shm. No shared imports besides a tiny pure-python protocol package that must be 3.9-compatible syntax (no match statements, no X | Y types).
- Hardware: 2x RTX 5060 Ti 16GB (Blackwell). Treat as two independent workers. Pin via CUDA_VISIBLE_DEVICES.
- Engine provides: efg/ (game spec + compiler), solvers/stagewise (CFR, CFR+, DCFR, PCFR+; reset/set_payoffs/set_root_ranges), games/ (kuhn, hunl river/turn, libratus loader), eval/ (exploitability, best response, strategy export).
Rules:
- Work in phases; stop at each phase gate and report evidence (commands + output).
- Write tests with pytest; mark GPU tests with @pytest.mark.gpu.
- Keep a docs/PROGRESS.md log with decisions and measured numbers.
- Never fabricate benchmark numbers; run them.
- Submodule URL: use https://github.com/lbn187/GPU-CFR directly (upstream, read-only). Do not push to it.
- No sudo. If a system package, NVIDIA driver or CUDA toolkit is missing, stop and report exactly what is needed.
- Work on a feature branch per phase and open a PR to main. Never push to main directly.
- Add a .gitignore for .venv-*, __pycache__, *.npy, and benchmark scratch output.