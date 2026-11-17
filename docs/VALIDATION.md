# Validation summary

How gpuThSlv is verified, and where it stands. Date: 2026-11-17.

## Open items

None unresolved. Known *explained* divergences (all carry a D-numbered cause
in [PHASE7.md](PHASE7.md) / [PHASE8_5.md](PHASE8_5.md)):

- **Frequency-only explained verdicts** — `pp-river-3sizes`,
  `pp-river-allin`, `ps-turn-diff`, `w-raise-cap2-dry`, `w-raise-cap2-wet`,
  `xraise-mixed`, `turn-narrow-a-allin`, `river-cap2-eq`, `river-cap3-eq`:
  EV within tolerance, weighted frequency differences on raise-boundary /
  equal-EV mixing lines (D1/D2/D7, non-unique equilibrium).
- **`strategy_sum` dtype** — the compiled engine keeps the average-strategy
  accumulator in float64 even when `dtype=float32` is requested; the regret
  buffers and exported strategy honor the requested dtype. Asserted in
  `tests/test_solver_engine.py::test_dtype_reaches_solver_buffers_cpu`.

Resolved this phase: **turn-narrow-a** — the D2-ext explanation (TS's
raise_limit allows a `b5→r17→r30` re-raise our cap-1 street removes) was
verified by re-running on structurally equal trees: cap 3 → xeval 0.041%,
cap 1 all-in raise → 0.347% ([PHASE8_6.md](PHASE8_6.md)).

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
| Phase 8.6 equal-tree raises (`scripts/phase8_6/runs/suite8_6.json`) | 4 | 1 | 3 | 0 |
| Phase 8 cross-eval (13 identical-tree spots) | 13 | — | — | all ≤ ~0.001pp of TS's own exploitability |
| Phase 8.5 dtype re-run (`float32_rerun.json`) | 4 | — | — | f32 vs f64 differ ≤3e-6 chips; f32 bitwise deterministic |
| Phase 9 API-side EV walk (`scripts/phase9/runs/ev_validation.json`) | 6 cap-0 river | — | — | 1,657 node/combo/action EVs, max \|Δ\| = 0.000000 vs the dump_ev walk; root EV = `dump_ev` = `profile_value_p0` |

**Phase 9 — API EV walk.** `holdem_solver/protocol/evwalk.py` is a pure-Python
reimplementation of the Phase 7 `dump_ev` evaluation walk (reach products,
chance-node averaging, `rank7` showdown). On the 6 river raise_cap=0 spots it
was checked three ways (`scripts/phase9/validate_node_evs.py`): root EV on the
TS-dumped tree equals `dump_ev` exactly; per-combo per-action EVs equal
`scripts/phase8/xeval.py::node_action_evs` at every shared node (1,657 values,
max |Δ| 0.000000); and the same walk on our own strategy equals the reported
`profile_value_p0` status value. Equal-tree / node-count invariants were
re-verified on 10 spots (`scripts/phase9/runs/tree_gate.json`).

**Turn validation is full** (Phase 8.5, confirmed in 8.6): emitting
`set_bet_sizes oop,<street>,donk,<sizes>` closes the Phase-8 donk gap —
w-turn-cap0 now cross-evaluates at 0.069% of pot on an identical tree, and
turn-narrow-a re-runs on structurally equal trees at 0.041% (cap 3) /
0.347% (cap 1 all-in).

## Tolerances

Fixed before each phase's runs (PHASE8_5.md lists them verbatim): both
solvers to ≤0.5% of pot exploitability; tree equivalence = equal action sets
root + 3 levels, amounts within 0.5 chip / 1%; |ΔEV| ≤ 1.0% of pot;
reach-weighted mean frequency diff ≤5 pp clean / ≤15+≤40 explained;
cross-eval ≤1.0% of pot.

## Known semantic differences (D-numbers)

D1 integer rounding, D2/D2-ext raise counting (TS: shared per-street
counter, bet counts, `raise_limit=4`; ours: raise actions per street,
`raise_cap` — equal at cap 3), D3 all-in snap threshold, D4 donk leads
(configurable — emitted since 8.5), D5 action order, D6 dump depth,
D7 auto-all-in at the cap boundary, D8 dead deal stubs, D9 dump=average vs
printed=current, D10 `*` ranges. Full table in [PHASE7.md](PHASE7.md); D4
update in [PHASE8_5.md](PHASE8_5.md); D2 source-read correction in
[PHASE8_6.md](PHASE8_6.md).

## Not validated

- **Flop trees** — never built or compared; the compiled engine ships
  river/turn HUNL games only. Every spot since Phase 7 starts on the turn
  or river.
- **Multiway (3+ players)** — solver, evaluator and REST model are
  two-player only; TexasSolver is heads-up as well, so there is no
  reference to validate against.
- **Reverse cross-evaluation** (our strategy scored inside TS's
  evaluator) — impossible: the console binary exposes only
  `set_*`/`build_tree`/`start_solve`/`dump_result`; no strategy-load or
  node-locking command exists (checked its command table, Phase 8).
- **Raise caps without an equal-tree check** — caps 0–3 now each have a
  verified equal-tree spot (cap 0 in Phase 7; caps 1–3 in Phase 8.6).
  `raise_cap ≥ 4` is untested and strictly deeper than TS (which stops at
  3 raises); `("x",m)` raise sizes are probed at two multipliers only.
- **Skipped inside phases 7–8.5** —
  - Phase 7 step 9: turn trees at `raise_limit=4` and the cap-1 tree check
    (both done in 8.6); donk *amount* equality (our donk sizes vs TS's
    `donk` pct semantics) unprobed — size counts only; turn frequency
    comparison (Phase 7 compared EVs/action sets only on turn).
  - Phase 8 step 4: turn-suite donk matching (done in 8.5) and cap-3 vs
    `raise_limit` (done in 8.6); the EV-gap walker is river-only, so turn
    spots skip it (cross-eval stands in).
  - Phase 8.5: river raises beyond cap 1 (done in 8.6); donk pct
    semantics still unprobed.
  - float32 vs float64 checked on 4 river spots only; the 10-board random
    batch is cap-0 standard sizes only.
- **Scale** — every validated spot is a small range/stack/tree; nothing is
  proven about large trees or long solve times.

## Test suites

`scripts/run_all_tests.sh` runs both venvs' pytest suites plus a
collection-coverage check (fails if any `tests/test_*.py` is collected by
neither venv). GPU tests are marked `@pytest.mark.gpu`.
