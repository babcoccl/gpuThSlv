# Phase 4 — Hardening scheduling and operations

Date: 2026-11-16. Branch: `devin/phase4-hardening`.

## NaN / duplicate exploitability in the Phase 3 river log

Probe: `scripts/phase4/probe_exploitability.py` on the same full-range river spot
(`KsJsTh7d2c`, `*` vs `*`, pot bets; 9,631,711 nodes). `--mode direct` calls the
engine's `TwoPlayerZeroSumEvaluator` in-process every 250 iterations;
`--mode worker` repeats the Phase 3 path and also logs `exploitability_iteration`
and the worker's per-eval `exploitability_history`. Logs:
`scripts/phase4/logs/nan_probe_{direct,worker}.jsonl`.

Verdict: **the NaN and duplicates come from our wrapper's logging, not the engine.**
The one real engine quirk is the final `0.0`.

1. **No NaN anywhere.** 17 direct evals (iterations 0–4000): every
   `BestResponseResult` field was finite, and `average_strategy_flat()` had 0
   NaN slots every time. The `null`s in `river_full_convergence.jsonl` are
   `exploitability: None`, meaning "no eval yet". The worker's `progress()`
   returns `None` until the first `progress_every` eval finishes. The first
   eval is late because `SolverEngine` builds the evaluator lazily on first
   use (3.49 s for this tree), so the first three 2 s polls showed
   iteration 3/250 with `null`.
2. **The duplicates are poll snapshots, not repeated evals.**
   `record_convergence.py` printed `client.progress()` from the
   `solve(on_progress=...)` callback, which fires every 2 s
   (`_WAIT_POLL_SECONDS`) whether or not a new eval has landed. One eval
   happens per ~3.2 s (250 iterations at ~78 it/s), so roughly every other
   poll repeated the last value. The worker log shows the same
   `exploitability_iteration` on both lines of each duplicated pair, and
   `exploitability_history` has exactly 16 evals with no repeats.
3. **The Phase 3 table was off by one eval.** Each poll line paired the *live*
   `iteration` with the *last finished* eval, and the script did not log
   `exploitability_iteration`. Rows were labelled 250 iterations late: the
   value shown at "500" is the 250-iteration eval (direct 0.04155), and so on.
   The corrected curve is below.
4. **Eval cost was misreported.** A full best-response eval takes 25 ms here,
   not ~2 s; the 2 s cadence was the poll interval. Throughput is ~78 it/s
   (the CFR steps), and evals are <1% of it.
5. **Final `exploitability = 0.0` is the engine clamping a negative NashConv.**
   At 4000 iterations `br_value_p0 + br_value_p1 = -0.000149`. That is inside
   the evaluator's `_negative_tolerance = 0.000229` (float32), so the engine
   reports `nashconv = 0.0`. The two best-response values are each ~0.744
   and are summed in float32 over 9.6M nodes, so the error near 1e-3 is about
   1e-4. Repeat evals of the same strategy are also not bitwise identical
   (non-deterministic GPU reductions). Below ~2e-3, the exploitability of this
   tree in float32 is noise-dominated. This explains the Phase 3 upticks
   (2250→2750, 3250→3750).
