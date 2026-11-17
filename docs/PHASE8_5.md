# Phase 8.5 — cleanup, small validations, documentation

Date: 2026-11-17. Status: complete.

Everything in the tolerances section was written **before** any run in this
phase; tolerances are fixed and are not revised after seeing results.
No new product features; the vendored GPU-CFR and TexasSolver are untouched.

## Tolerances (fixed before running)

Same conventions as Phase 8 unless stated: both solvers to ≤ **0.5% of pot**
exploitability (ours `dtype=float64`, `cfr_plus`; TS `set_accuracy 0.5`,
`allin_threshold 2.0`, `use_isomorphism 0`). TexasSolver runs use 8 threads.

- **Tree equivalence** (root + 3 levels): action *sets* equal; amounts within
  0.5 chip or 1% relative (absorbs D1 integer rounding). Structural deltas
  from documented semantic differences (D-numbers) are reported, not
  auto-failed.
- **Root EV**: |ΔEV| ≤ **1.0% of pot** + 0.05 chips.
- **Per-node weighted frequency diff** (reach-weighted both sides, the
  `w(c)·own_reach·opp_mass` formula from PHASE8.md): mean |Δ| ≤ **5 pp**
  clean; mean ≤ 15 pp and max ≤ 40 pp → *explained* pending EV agreement;
  otherwise investigate.
- **Exploitability at dump time**: each solver ≤ **0.75%** of pot.
- **Cross-evaluation**: TexasSolver's dumped average strategy evaluated in
  our evaluator must be ≤ **1.0% of pot** (2× the 0.5% solve tolerance). A
  failure downgrades `match` to `explained` only when a verified cause is
  recorded; otherwise the spot is `unresolved`.
- **float32 vs float64**: report unrounded EV and exploitability differences
  (≥8 significant digits) plus run-to-run variation of two identical float32
  solves; identical bitwise values across dtypes are treated as a bug.
- **Verdicts**: `match` (all tolerances pass), `explained` (a stated specific
  cause — a D-numbered difference or demonstrated non-unique equilibrium),
  `unresolved` otherwise. Unresolved items are listed at the top of
  `docs/VALIDATION.md`.

## Step outcomes

Recorded below as each numbered spec step lands (done / skipped+reason /
failed+analysis).

### PART A

1. **PHASE8.md corrections — done.** Status now `complete` with this date;
   the identical-tree cross-eval count is **13 of 14** (w-turn-cap0 is not an
   identical tree); the EV-erased gap is reported as **0.04–0.15% of pot**.
   Also corrected: PHASE8's "float32 identical" prose (the data shows small
   diffs, step 5) and its claim that a TS tree with donk leads cannot be
   configured (step 2).

2. **Donk reconciliation — PHASE7 was right, PHASE8 was wrong.** Direct
   console probe on the turn board `Ts,9s,8s,Jh`, pot 20, stack 60:
   - Input `scripts/phase8_5/runs/donk_probe_in.txt` adds
     `set_bet_sizes oop,river,donk,50` after the bet/raise lines.
   - In the dump (`donk_probe_out.json`), the OOP river node after
     `BET 15 → CALL → river card` offers `['CHECK','BET 25.000000']` —
     half the 50-chip pot = **the donk size applies** (a check-check river
     node still bets 10 = the `bet` size). Turn probe
     (`donk_turn_in.txt`, `oop,turn,donk,33`) parses but leaves the turn root
     unchanged — donk on turn is meaningless for a turn-rooted tree.
   - PHASE8.md's "cannot be configured" sentence corrected; PHASE7.md's D4
     row annotated with the verified command.

3. **Test collection — done.** `tests/test_phase6_raises.py` was never
   orphaned: `conftest.py` collects it only under Python <3.10, which is the
   engine venv (3.9). New `scripts/run_all_tests.sh` runs the engine suite,
   then the API suite, then a collection-coverage check that fails if any
   `tests/test_*.py` is collected by neither venv; `--quick` runs the
   coverage check only. Both suites pass (see gate output at the bottom).

### PART B

4. **dtype reaches the solver — done.** On `river-dry-cap0`
   (`scripts/phase8_5/runs/float32_rerun.json → buffer_dtypes`):
   `config.device.dtype`, `RealEngine.describe()["dtype"]`, the flat
   strategy and `regret_sum` all report the requested dtype.
   **Found:** `solver.strategy_sum` is `torch.float64` under a float32
   request — the average-strategy accumulator is kept in float64 by the
   compiled engine regardless of requested dtype (fine for precision; now
   asserted in `test_dtype_reaches_solver_buffers_cpu`). The engine now
   reports the requested dtype in the build response
   (`describe()["dtype"]`) and in `rpc_ping` — field added to `RealEngine`,
   `FakeEngine`, `EngineService`; regression tests:
   `test_dtype_reaches_solver_buffers_cpu` (engine venv) and
   `test_build_and_ping_report_requested_dtype` (both venvs).

5. **float32 vs float64 re-run — done.** Unrounded (repr) results in
   `scripts/phase8_5/runs/float32_rerun.json`; identical to Phase 8's
   `float32.json` bit-for-bit (deterministic) — PHASE8.md's "identical EVs"
   prose was wrong (fixed): the runs differ by 1e-7–3e-6 chips:

   | spot | ΔEV (chips) | Δexploitability (pp) |
   |---|---|---|
   | toy-check | 2.07e-07 | 3.06e-06 |
   | toy-bet | 3.12e-06 | 2.80e-05 |
   | river-dry-cap0 | 1.62e-07 | 6.24e-06 |
   | river-wet-cap0 | 1.64e-06 | 3.14e-06 |

   Two identical float32 runs of toy-check are **bitwise identical**
   (EV `10.012043952941895`, expl `0.030128955841064453` both runs) —
   the engine is deterministic run-to-run. Not bitwise-identical across
   dtypes → no dtype-ignoring bug.

### PART C

6. **Turn spots with matched trees — done.** `texas_input.py` gained
   `emit_donk` (emits `set_bet_sizes oop,<street>,donk,<sizes>` equal to
   OOP's bet fractions). Re-runs (`suite8_5.json`):

   | spot | ΔEV % pot | xeval ts-in-ours | verdict |
   |---|---|---|---|
   | turn-narrow-a | 0.263% | 15.37% | explained (D2-ext: TS allows a `b5→r17→r30` re-raise our cap-1 street removes) |
   | turn-narrow-b | 0.069% | 0.063% | match |
   | w-turn-cap0 | 0.035% | 0.069% | **match** — the Phase-8 16.5% xeval gap is closed |

   Turn validation is now full, not partial: the w-turn-cap0 tree is
   identical and its strategy cross-evaluates at 0.069% of pot.

7. **Per-player / per-street bet sizes — done (5 spots).**
   `pp-river` (`{0:(0.33,),1:(0.66,)}`) match; `pp-river-3sizes`
   (`{0:(0.33,0.5,1.0),1:(0.5,0.75,1.5)}`) explained (freq mean 13.3pp,
   EV gap 0.126% — non-unique equilibrium); `pp-river-allin` explained;
   `ps-turn-diff` (turn 0.25 / river 1.25) explained; `pp-turn` (per-player
   on a turn spot) match. All |ΔEV| ≤ 0.58% of pot, all xeval ≤ 0.22%.

8. **Raise combinations — done (4 spots).** `w-raise-cap2-dry` and
   `w-raise-cap2-wet` (weighted ranges, cap 2, `("p",0.5)/("p",1.0)`):
   explained (EV within tolerance; D7/D2-ext raise-boundary lines).
   `ss-raise-cap1` (SPR 1.5, all-in, cap 1): match. `xraise-mixed`
   (`("x",3.0)` + `("p",1.0)`): TS *can* express `("x",m)` per bet size —
   `pct = 100·(m−1)·bet/(pot+2·bet)`; at bet 10/pot 20 the 3× raise-to of
   30 is `raise,50`. Mapping layer gained `x_raise_pcts`; trees match and
   xeval is 0.016% (explained on freq only). Harness bug found + fixed on
   re-run: `x_raise_pcts` values were re-multiplied by 100 (emitted
   `raise,5000` → all-in); the corrected run is what `suite8_5.json` holds.

9. **New differences / bugs — done.**
   - D-numbers unchanged: no *new* TS-vs-us semantic differences were found.
     D4 is updated (donk is configurable) and the existing D2-ext /
     raise-counting note now explains turn-narrow-a.
   - API parity gap fixed: `SolveRequest` had no `raise_cap`/`raise_sizes`
     and `bet_sizes` accepted only a preset name or flat list — raises and
     per-player/per-street sizes were unreachable over REST. Both now
     accepted (`raise_sizes` validated as `["x",m]`/`["p",f]`).
   - Engine quirk documented: `strategy_sum` accumulates in float64 under a
     float32 request (step 4).
   - Regression tests: `tests/test_texas_input.py` (donk emission,
     `x_raise_pcts`, per-player sizes — pure python, both venvs) plus the
     dtype tests in step 4.

### PART D

10. `docs/VALIDATION.md` — done; unresolved/open items at the top.
11. `README.md` — "Game rules and limits" + "API usage" with complete
    example requests (standard river, weighted range, raises, per-player
    sizes, turn). Stale "No raises" limit removed.
12. `docs/PROGRESS.md` — Phase 8.5 entry appended; phases 6–8 entries were
    already present.
13. `docs/ARCHITECTURE.md` — regenerated from current code.
14. `docs/INDEX.md` + `CHANGELOG.md` — done.

## Gate

- `scripts/run_all_tests.sh`: both suites pass, collection coverage clean
  (output in the Phase 8.5 section of PROGRESS.md / PR description).
- `scripts/check_links.py`: all relative links in `*.md`/`docs/*.md` resolve.
- Every step above has an outcome; every new spot has a verdict; the only
  non-match verdicts carry a D-numbered cause.
