Project: GPU-accelerated Texas Hold'em CFR solver exposed via REST API (Linux).
Core engine: https://github.com/lbn187/GPU-CFR (fork it into our org; vendor as a git submodule at third_party/GPU-CFR).
Critical facts:
- The engine ships only COMPILED .so modules (CPython 3.9 x86_64-linux ABI). No source. Learn the API via help(), README, and tests. Never try to edit or decompile it.
- Use Python 3.9 for the whole project.
- Hardware: 2x RTX 5060 Ti 16GB (Blackwell). Treat as two independent workers. Pin via CUDA_VISIBLE_DEVICES.
- Engine provides: efg/ (game spec + compiler), solvers/stagewise (CFR, CFR+, DCFR, PCFR+; reset/set_payoffs/set_root_ranges), games/ (kuhn, hunl river/turn, libratus loader), eval/ (exploitability, best response, strategy export).
Rules:
- Work in phases; stop at each phase gate and report evidence (commands + output).
- Write tests with pytest; mark GPU tests with @pytest.mark.gpu.
- Keep a docs/PROGRESS.md log with decisions and measured numbers.
- Never fabricate benchmark numbers; run them.