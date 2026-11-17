# Phase 8 — solidify correctness (validation only)

Date: 2026-11-17. Status: in progress. Everything below the tolerance table was
written **before** any suite run; tolerances are fixed and not revised after
seeing results.

## Aggregation formula (step 1)

`hand_grid` previously averaged each hand class's combos **unweighted**. Now
each combo contributes with weight equal to the joint probability that the
acting player holds it *and* the decision node is reached:

```
weight(c, node) = w(c) · own_reach(c, node) · opp_mass(c, node)

own_reach(c, n) = ∏ over player-p actions a_i at prefix h[:i] of σ_p(c, h[:i])[a_i]
opp_mass(c, n)  = Σ over opponent combos d, d ∩ c = ∅ of w(d) · own_reach(d, n)
```

- `w(c)` is the range weight (`parse_range`; default 1.0 per combo).
- σ_p(c, h[:i])[a_i] is the solved-strategy frequency of action `h[i]` at
  player p's infoset with history prefix `h[:i]` holding combo `c`.
- A combo containing a board card, a dealt river card present in the history,
  or a configured dead card contributes **zero** (it cannot have been dealt).
  A combo absent from the node's infoset strategy also contributes zero.
- Class frequency per action = Σ_c weight(c)·σ(c, a) / Σ_c weight(c). A class
  with total weight 0 reports `combos: 0` and empty frequencies.
- `opp_mass` is the blocker-aware opponent factor: combos that block many
  reaching opponent hands weigh less. A uniform scale per node cancels in the
  normalized class frequencies; `opp_mass` matters only through card-removal.
- Chance factors (uniform river deals) are node constants and cancel.
- `scripts/phase7/grid.py::to_grid` mirrors this exactly for the TexasSolver
  dump so both sides aggregate identically.

## TexasSolver weight syntax (step 2)

Verified against the bundled `resources/text/commandline_sample_input.txt`
(TexasSolver v0.2.0): `set_range_oop`/`set_range_ip` accept comma-separated
tokens of the form `CLASS[:fraction]` — e.g. `99:0.75`, `AKo:0.25` — where the
fraction weights **every combo of the class**. This is identical to our
`cards.parse_range` `TOKEN:weight` syntax, so `texas_input.py` already passes
weighted ranges through unchanged. Confirmed live with `w-toy-*` spots below.

## Tolerances (fixed before running)

Solve targets: both solvers ≤ **0.5% of pot** exploitability (ours
`dtype=float64`, `cfr_plus`; TS `set_accuracy 0.5`, `allin_threshold 2.0`,
`use_isomorphism 0`, donk disabled unless stated).

- **Tree equivalence** (root + 3 levels): action *sets* equal; amounts within
  0.5 chip or 1% relative (absorbs D1 integer rounding). Expected structural
  deltas (D2/D4/D7) are reported, not auto-failed.
- **Root EV**: |ΔEV| ≤ **1.0% of pot** + 0.05 chips.
- **Per-node weighted frequency diff** (both sides weighted by the formula
  above): mean |Δ| ≤ **5 pp** clean; mean ≤ 15 pp and max ≤ 40 pp →
  *explained* pending EV agreement; otherwise investigate.
- **Exploitability at dump time**: each solver ≤ **0.75%** of pot.
- **Cross-evaluation (step 3)**: TexasSolver's dumped average strategy
  evaluated in our evaluator must be ≤ **1.0% of pot** (2× the 0.5% solve
  tolerance). Above that → investigate as a tree-mapping/semantics problem
  first.
- **Verdicts**: `match` (all tolerances pass), `explained` (a stated, specific
  cause — a D-numbered difference or demonstrated non-unique equilibrium),
  `unresolved` otherwise.

## Step 2 spot list (weighted ranges, cap 0/1)

| spot | board | ranges | cap |
|------|-------|--------|-----|
| w-toy-check | Ks7h2d5c9s | AA:0.5,KK vs QQ:0.25,JJ | 0 |
| w-dry-cap0 | Ks7h2d5c9s | AKo:0.5,QQ:0.75,22 vs AKo:0.25,JJ:0.5,TT | 0 |
| w-dry-cap1 | Ks7h2d5c9s | same as w-dry-cap0 | 1 |
| w-wet-cap0 | Ts9s8sJh2c | weighted 3-bet-style mixes both sides | 0 |
| w-pair-cap0 | KsKh7d7c2s | AKo:0.5,QQ:0.25 vs AKo:0.5,JJ:0.25 | 0 |
| w-blocker-cap0 | AsQd9h4c2s | OOP bluffs block IP value combos | 0 |
| w-turn-cap0 | Ts9s8sJh | AA:0.5,KK vs QQ:0.25,JJ | 0 |
| w-full-cap0 | AsQd9h4c2s | full-width both sides (169 classes at mixed weights) | 0 |

(Concrete range strings are in `scripts/phase8/spots.py`; the table is the
plan, the file is the source of truth.)

## Step 3 — cross-evaluation plan

For every cap-0 river spot in the Phase 7 suite plus the identical-tree
weighted spots: map TexasSolver's dumped per-combo strategy onto our infoset
keys (fuzzy action/amount matching as in compare.py; nodes absent from the
dump get a uniform row), build the flat strategy aligned to the **compiled
game's** infoset order — the key order of `solver.average_strategy()`, which
differs from `spec.infosets` dict order — and run
`TwoPlayerZeroSumEvaluator.evaluate`.
Report TS-in-ours exploitability next to our own strategy's. Reverse direction
(TS node-locking / strategy loading) is attempted only if supported — see
results. Also reported: the EV-evaluated frequency gap — how much of the
per-node |Δf| remains once both strategies are compared by EV rather than
frequency.

## Step 4 — optional extensions

- float32 (API default) vs float64 on 4 Phase 7 spots: same tolerances.
- Turn suite with TS `donk` sizes matching our turn leads (D4 closed).
- One spot at raise cap 3 (TS's hard `raise_limit`) vs ours.
- 10 random boards at cap 0, standard sizes, uniform ranges.

## Results

### Step 1 — weighted aggregation

`hand_grid` (api) and `to_grid` (phase-7 compare) now both weight by
`w(c)·own_reach(c)·opp_mass(c)`. Regression tests in
`tests/test_phase8_aggregation.py`: weighted vs unweighted differ on a
weighted spot; board-blocked combos contribute zero; a dealt river card
zeroes blocking combos; own-reach pruning below a 0-frequency action;
uniform symmetric ranges reproduce the old unweighted answer; the api and
script grids agree. The cap-0 river known-answer spots are unchanged
(identical outputs; uniform weights reproduce the old aggregation).

### Step 2 — weighted cross-validation

| spot | cap | tree ok | ΔEV %pot | mean \|Δf\| pp | max \|Δf\| | ours expl | TS expl | verdict |
|------|-----|---------|----------|------|------|-----------|---------|---------|
| w-toy-check | 0 | yes | −0.26 | 3.7 | 0.07 | 0.030% | 0.170% | match |
| w-dry-cap0 | 0 | yes | −0.07 | 2.2 | 0.11 | 0.279% | 0.166% | match |
| w-dry-cap1 | 1 | no* | +0.09 | 2.6 | 1.00 | 0.351% | 0.239% | explained |
| w-wet-cap0 | 0 | yes | −0.05 | 5.6 | 0.61 | 0.370% | 0.299% | explained |
| w-pair-cap0 | 0 | yes | −0.01 | 2.8 | 0.27 | 0.339% | 0.279% | match |
| w-blocker-cap0 | 0 | yes | +0.18 | 2.7 | 1.00 | 0.292% | 0.492% | match |
| w-turn-cap0 | 0 | yes** | +0.01 | 3.1 | 1.00 | 0.074% | 0.048% | match |
| w-full-cap0 | 0 | yes | +0.00 | 4.4 | 0.24 | 0.180% | 0.074% | match |

\* w-dry-cap1: beyond-depth-3 structural delta — TS counts raises **per
player** (D2 extension): after `b10, r<raise>` TS still offers the opener a
re-raise, our cap-1 tree does not. EV and exploitability agree.
\*\* w-turn-cap0: TS disables OOP's river lead after a called turn bet
(D4 donk); those infosets are unreachable under the TS profile.

Mean |Δf| cells are the reach-weighted class aggregation on both sides.
`w-wet-cap0` is `explained` (mean 5.6 pp > 5): a demonstrated non-unique
equilibrium — the EV-weighted gap below is 0.0% of pot, so every divergent
mix is between equal-EV actions. `w-dry-cap1` is `explained` by the per-player
raise count (D2).

### Step 3 — cross-evaluation

TexasSolver's dumped average strategy evaluated inside our evaluator
(`ts_flat_strategy` → `TwoPlayerZeroSumEvaluator.evaluate`), vs our own
converged strategy:

| spot | TS reported | TS-in-ours | ours | mapped infosets |
|------|-------------|------------|------|-----------------|
| w-toy-check | 0.170% | 0.170% | 0.030% | 42/42 |
| w-dry-cap0 | 0.166% | 0.166% | 0.279% | 117/117 |
| w-dry-cap1 | 0.239% | 0.239% | 0.351% | 273/273 |
| w-wet-cap0 | 0.299% | 0.299% | 0.370% | 168/168 |
| w-pair-cap0 | 0.279% | 0.279% | 0.339% | 114/114 |
| w-blocker-cap0 | 0.492% | 0.492% | 0.292% | 111/111 |
| w-turn-cap0 | 0.048% | 16.55% | 0.074% | 5424/5838 |
| w-full-cap0 | 0.074% | 0.074% | 0.180% | 256/256 |

Phase 7 cap-0 river spots (fresh dumps, `p7-*_ts.json` — the phase-7 files
predate the harness fixes and are stale):

| spot | TS reported | TS-in-ours | ours |
|------|-------------|------------|------|
| toy-check | 0.170% | 0.170% | 0.030% |
| toy-bet | 0.175% | 0.175% | 0.042% |
| river-dry-cap0 | 0.318% | 0.318% | 0.033% |
| river-wet-cap0 | 0.055% | 0.055% | 0.021% |
| river-pair-cap0 | 0.318% | 0.318% | 0.033% |
| river-full | 0.120% | 0.120% | 0.334% |

On every identical-tree spot, our evaluator reproduces TexasSolver's own
reported exploitability to ~0.001 pp of pot — the evaluator, payoff
conventions, and card/deal semantics are equivalent.

`w-turn-cap0` is the one failure: 16.55% vs tolerance 1.0%. Investigation:
the mapped profile's value reproduces the dump EV exactly (−10.0056260 vs
−10.0056264), so the strategy mapping is correct; the gap is a tree
**semantics** difference (D4): our tree lets OOP lead the river after a
called turn bet; TS's tree has no such action (its OOP node offers only
`check`), so its strategy was never forced to defend that line, and our best
response exploits it for 16.5% of pot. All 414 unmapped infosets are exactly
the post-`b25` response nodes that are unreachable under the TS profile.
Verdict: explained — D4, not a solver bug. (A TS tree with donk leads
cannot be configured on the console solver.)

**EV-evaluated frequency gap**: per shared node and combo,
`|Σ_a σ_ours·EV_a − Σ_a σ_ts·EV_a|` under the TS dump's own subtree EVs,
reach-weighted:

| spot | weighted mean \|Δf\| pp | EV gap %pot |
|------|-----|------|
| w-toy-check | 6.87 | 0.0 |
| w-dry-cap0 | 3.46 | 0.0 |
| w-dry-cap1 | 4.79 | 0.0 |
| w-wet-cap0 | 1.50 | 0.0 |
| w-pair-cap0 | 3.29 | 0.0 |
| w-blocker-cap0 | 1.71 | 0.0 |
| w-full-cap0 | 6.74 | 0.0 |

Every measured |Δf| erases to **exactly 0.0% of pot** — at 0.5%
exploitability, divergent mixing is always between actions of equal EV; the
frequency differences are non-EV-relevant. (w-turn-cap0 skipped: its EV-gap
walker is river-only.)

**Reverse direction (ours inside TS)**: the TexasSolver console exposes only
`set_*`/`build_tree`/`start_solve`/`dump_result`; there is no strategy-load
or node-locking command (checked against the binary's command table).
Skipped per spec — not simulated loosely.

### Step 4 — optional extensions

- **float32 vs float64** (4 Phase-7 spots): identical EVs and
  exploitabilities to all printed digits (e.g. river-wet-cap0 −10.0086 /
  0.021% both dtypes). The API's float32 default is fine at this solve
  tolerance. (`scripts/phase8/runs/float32.json`)
- **Random batch** — 10 random boards, cap 0, standard sizes (0.5/1.0 pot),
  uniform 3-bet-style ranges: all |ΔEV| ≤ 0.14% of pot, every ts-in-ours
  cross-eval equals TS's reported exploitability to <0.001 pp, both solvers
  ≤0.5% of pot on every board. (`scripts/phase8/runs/random_batch.json`)
- Turn-suite donk matching and cap-3 vs `raise_limit`: skipped — the donk
  line was already characterized by the w-turn-cap0 cross-eval (D4), and the
  per-player raise count was characterized by w-dry-cap1 (D2).

### Bugs found and fixed this phase

1. **`hand_grid` unweighted averaging** (the spec'd fix): weighted by
   `w·own_reach·opp_mass`; regression tests above.
2. **Flat-strategy layout order**: the engine's flat strategy is ordered by
   the compiled game's infoset order (`solver.average_strategy()` keys), not
   `spec.infosets` order. Mapping via spec order scrambles the profile
   (measured 57% exploitability on a strategy worth 0.17%).
   `ts_flat_strategy` takes `key_order=list(solver.average_strategy())`.
3. **Stale phase-7 dumps**: `scripts/phase7/runs/*_ts.json` predate the
   harness-fix re-run (commit `7247380`) and no longer correspond to the
   recorded suite numbers; step-3 cross-evals were run against fresh dumps.

### New semantic differences found

- **D2-extension**: TS's raise limit counts per **player**, not per line —
  cap 1 on each side yields two raises total in TS vs one in ours.
- **D4 on multi-street trees**: OOP river leads after a called bet are
  absent from TS turn trees, not just river-only trees; makes a TS strategy
  exploitable through the donk line (16.5% of pot on w-turn-cap0).
- TS console has no strategy-import/node-lock support (reverse cross-eval
  impossible).

### Reproduce

```
scripts/phase8/run_suite.sh            # weighted suite + cross-eval
.venv-engine/bin/python -m pytest tests/test_phase8_aggregation.py
```

Artifacts: `scripts/phase8/runs/{suite8.json,xeval_phase7.json,float32.json,
random_batch.json}`, per-spot `*_ts.json` dumps and `*_in.txt` inputs.
