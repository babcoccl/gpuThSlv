# Validation summary

How gpuThSlv is verified, and where it stands. Date: 2026-11-17.

## Open items

None unresolved. Known *explained* divergences (all carry a D-numbered cause
in [PHASE7.md](PHASE7.md) / [PHASE8_5.md](PHASE8_5.md)):

- **turn-narrow-a** — D2-ext: TexasSolver counts raises per player
  (`raise_limit = 4` including the opening bet); ours caps raises per street.
  At `raise_cap=1` TS keeps a `b5→r17→r30` re-raise line our tree removes, so
  TS's frequencies at shared nodes price in a line that cannot exist for us
  and cross-evaluate at 15.4% of pot. Root EVs agree (0.26%).
- **Frequency-only explained verdicts** — `pp-river-3sizes`,
  `pp-river-allin`, `ps-turn-diff`, `w-raise-cap2-dry`, `w-raise-cap2-wet`,
  `xraise-mixed`: EV within tolerance, weighted frequency differences on
  raise-boundary / equal-EV mixing lines (D1/D2/D7, non-unique equilibrium).
- **`strategy_sum` dtype** — the compiled engine keeps the average-strategy
  accumulator in float64 even when `dtype=float32` is requested; the regret
  buffers and exported strategy honor the requested dtype. Asserted in
  `tests/test_solver_engine.py::test_dtype_reaches_solver_buffers_cpu`.

## What is validated

Cross-validated against TexasSolver v0.2.0 (`scripts/phase7`, `phase8`,
`phase8_5`): tree shape, root EV, per-node reach-weighted strategy
frequencies, and cross-evaluation (TS's dumped average strategy scored in
our evaluator).

| Suite | Spots | Match | Explained | Unresolved |
|---|---|---|---|---|
| Phase 7 (`scripts/phase7/runs/suite.json`) | 15 | 4 | 11 | 0 |
| Phase 8 weighted (`scripts/phase8/runs/suite8.json`) | 8 | 6 | 2 | 0 |
| Phase 8.5 (`scripts/phase8_5/runs/suite8_5.json`) | 12 | 5 | 7 | 0 |
| Phase 8 cross-eval (13 identical-tree spots) | 13 | — | — | all ≤ ~0.001pp of TS's own exploitability |
| Phase 8.5 dtype re-run (`float32_rerun.json`) | 4 | — | — | f32 vs f64 differ ≤3e-6 chips; f32 bitwise deterministic |

**Turn validation is full** (Phase 8.5): emitting
`set_bet_sizes oop,<street>,donk,<sizes>` closes the Phase-8 donk gap —
w-turn-cap0 now cross-evaluates at 0.069% of pot on an identical tree.

## Tolerances

Fixed before each phase's runs (PHASE8_5.md lists them verbatim): both
solvers to ≤0.5% of pot exploitability; tree equivalence = equal action sets
root + 3 levels, amounts within 0.5 chip / 1%; |ΔEV| ≤ 1.0% of pot;
reach-weighted mean frequency diff ≤5 pp clean / ≤15+≤40 explained;
cross-eval ≤1.0% of pot.

## Known semantic differences (D-numbers)

D1 integer rounding, D2/D2-ext raise counting per player vs per street,
D3 all-in snap threshold, D4 donk leads (configurable — emitted since
8.5), D5 action order, D6 dump depth, D7 auto-all-in at the cap boundary,
D8 dead deal stubs, D9 dump=average vs printed=current, D10 `*` ranges.
Full table in [PHASE7.md](PHASE7.md); D4 update in
[PHASE8_5.md](PHASE8_5.md).

## Test suites

`scripts/run_all_tests.sh` runs both venvs' pytest suites plus a
collection-coverage check (fails if any `tests/test_*.py` is collected by
neither venv). GPU tests are marked `@pytest.mark.gpu`.
