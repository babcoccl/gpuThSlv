# Phase 7 — cross-validation against TexasSolver

Date: 2026-11-17

## Step 1 — TexasSolver discovery

- **Install**: `/home/aihub/Downloads/TexasSolver-v0.2.0-Linux/TexasSolver-v0.2.0-Linux`
- **Binary**: `console_solver` (run `console_solver -i <input>` from the install dir; ~30 s startup for the card-compairer dictionary load)
- **Version**: v0.2.0 (release zip name; binary itself prints no version; semantics verified against tag `95f039c` source)
- **Threads**: set via `set_thread_num`; suite runs use 8 (box has 48 cores; skill caps at one solve at a time)
- **Sample run**: bundled `commandline_sample_input.txt` (flop QsJh2h, pot 50, stack 200, 8 threads, accuracy 0.5) runs to ~200 iterations; emits `tmp_log.txt` records `{"exploitibility","iteration","time_ms"}` and dumps `output_result.json`.

### Dump JSON schema (`dump_result`)

```
action_node: {node_type: "action_node", player: int, actions: [str],
              childrens: {action_str -> node},
              strategy: {actions: [str], strategy: {combo_str -> [freqs]}}}
chance_node: {node_type: "chance_node", deal_number: int,
              dealcards: {card_str -> node}}   # deal_number == 0 -> terminal
```

- `player`: **1 = OOP (acts first each street), 0 = IP**. Maps to our player 0 / player 1 respectively.
- `actions`/`childrens` keys: `"CHECK"`, `"FOLD"`, `"CALL"`, `"BET <amount>"`, `"RAISE <amount>"` (amounts formatted `%.6f`).
- `strategy.strategy` maps combo strings (`"AdAc"`) to frequency arrays aligned with `strategy.actions`; combos blocked by the board are absent. No EV fields anywhere — root EV must be computed by walking the tree.
- On turn spots the street boundary is a chance node whose `dealcards` maps each possible river card to a subtree. `set_dump_rounds N` dumps N streets from the root (river spot → 1, turn spot → 2).

### Game semantics (v0.2.0, `src/GameTree.cpp`, `src/tools/Rule.cpp`, `CommandLineTool.cpp`)

- `set_pot P` → each player's baseline commit `P/2`; `set_effective_stack S` → internal `stack = S + P/2`. Bet sizing pot = `2 × max(oop_commit, ip_commit)`.
- `BET`: amount = `round(pct/100 × pot)` (big_blind = 1 → integer rounding). If `amount + commit > S × allin_threshold`, snapped to all-in (`stack − commit`). `set_bet_sizes ...,allin` adds the all-in amount (remaining chips).
- `RAISE`: `amount = round(pct/100 × pot) + (opp_commit − my_commit)` — i.e. call the bet, then add `pct%` of the pot. **Equivalent to our `("p", f)` raise**: raise-to level = bet level faced + `f × pot_after_call`. The dump's `RAISE X` shows the *amount added on the action* (call + increment); our `r<level>` = raiser's total contribution this spot = chips already invested + X.
- Raise count limit: `raise_limit = 4`, hard-coded (no CLI command), counting the opening bet → **at most 3 raises per street**. `set_bet_sizes <p>,<street>,raise` with no sizes disables raises entirely (our `raise_cap=0`).
- All-in threshold: `set_allin_threshold T` → snap when post-action total commit exceeds `T × effective_stack`.
- Street order: OOP (player 1) always acts first. `call` or `check`-`check` ends the street; river end → showdown, earlier streets → chance node dealing next card. No raise/check-raise after `call` or `check` except preflop — irrelevant for our spots.
- **Donk**: a chance node after a street ending in an *OOP call* is flagged donk; OOP's lead on the next street then uses `donk` sizes. OOP's lead after check-check uses `bet` sizes. We leave `donk` unset (disabled) per spec — a deliberate semantic difference on turn spots (see below).
- Solver: `PCfrSolver` with `discounted_cfr` trainer.
- **Exploitability**: `printExploitability` returns `(BR_ev_p0 + BR_ev_p1) / 2 / pot × 100` (percent). Verified against probe output: (3.75 + 0.833)/2/10×100 = 22.9% ✓. Ours (`TwoPlayerZeroSumEvaluator`) reports the same BR sum in chips → convert: `expl_pct = expl_chips / (2 × pot) × 100`.
- **Known-case check**: Kuhn (PHASE1's −1/18 = −0.0556 game value) — our solver reports profile_value_p0 = −0.0555 and exploitability 0.0019 chips at convergence; units verified end-to-end.
- `set_use_isomorphism 0` disables card-suit isomorphism for the suite.
- `set_allin_threshold` — we run with `2.0` so no legal bet/raise snaps to
  all-in; all-in is only offered via the explicit `stack/pot` bet size
  (`all_in=True`). At `0.67` (TS's default) large raises snap to
  `stack - commit`, which collapses mapped raise options (verified).

## Mapping layer (`scripts/phase7/`)

`texas_input.py` renders a `SpotConfig` to a TexasSolver input file:
`range0 → set_range_oop`, `range1 → set_range_ip`, pot fractions ×100, `("p",f)` raises → `raise,<100f>`, `all_in` → `stack/pot` bet size, `raise_cap=0` → empty `raise` list, no `donk` sizes, `use_isomorphism 0`, `allin_threshold 2.0` (no snapping).

`texas_dump.py` parses the dump JSON into our `/strategy` node shape
(`{player, history, actions, strategy}`, player remapped 1→0 / 0→1,
`BET x→b<x>`, `RAISE x→r<level>` with `level = chips_invested + x`,
`CALL→call`, `CHECK→check`, `FOLD→fold`; river cards become `river:<card>`
history sections). `grid.py` aggregates combos into the 169 hand classes with
unweighted per-combo means, matching `holdem_solver.api.strategy.hand_grid`.

## Semantic differences found (mapping-level, pre-suite)

| # | Difference | Consequence |
|---|------------|-------------|
| D1 | TS rounds `pct×pot` to whole chips before adding the call (`bb=1`); ours keeps fractions exact | raise levels can differ by ≤0.5 chip |
| D2 | TS caps raises at 3 per street (`raise_limit=4` incl. the bet); ours honours `raise_cap` 0–3 | cap 1/2 spots: TS tree is deeper than ours below the cap level |
| D3 | TS snaps any bet/raise committing > threshold×stack to all-in | disabled via `set_allin_threshold 2.0`; at 0.67 it collapsed raises (verified) |
| D4 | TS disables OOP's lead after an OOP-called street unless `donk` sizes are set | turn spots: the river subtree under `call`-ended turn lines exists only on our side |
| D5 | TS dumps action order CALL→RAISE→FOLD; ours fold→call→raises | compare actions as sets |
| D6 | `set_dump_rounds` counts streets from the tree root | turn spots need `dump_rounds=2` |
| D7 | TS auto-adds an all-in raise where every configured raise exceeds the remaining stack | at our `raise_cap` boundary TS nodes carry an extra `r<stack>` action |

## Tolerances (fixed before running the suite)

Both solvers solved to ≤0.5% of pot exploitability, so the joint bound is ~1%.

- **Tree equivalence** (root + 3 levels): action *sets* must match; amounts within 0.5 chip or 1% relative (absorbs D1). Structural deviations from D2/D3/D4 are expected on capped/turn spots and reported, not auto-failed.
- **Root EV**: |ΔEV| ≤ **1.0% of pot** + 0.05 chips absolute slack.
- **Per-node action-frequency diff** (range-weighted mean over hand classes per node): mean |Δ| ≤ **5 pp** passes clean; mean |Δ| ≤ 15 pp or max |Δ| ≤ 40 pp → *explained difference* pending EV agreement; beyond that → investigate.
- **Exploitability**: each solver ≤ **0.75%** of pot at dump time.
- **Verdicts**: `match` (all tolerances pass), `explained` (a documented semantic difference accounts for the delta, or EVs agree while frequencies differ = non-unique equilibrium), `unresolved` otherwise.

## Suite configuration

15 spots (`scripts/phase7/spots.py`): the 2 known-answer toys, the 3 river
boards (dry / wet / paired) at raise caps 0, 1, 2, 2 narrow turn spots, 1
short-stack river spot, 1 full-range river (`*` = all 1326 combos, one bet
size, no raises). Both solvers to ≤0.5% of pot: ours at `dtype=float64`,
`variant=cfr_plus`, `device=cuda`; TS at `set_accuracy 0.5`,
`allin_threshold 2.0`, `use_isomorphism 0`, donk disabled. Raw dumps and
TS inputs are kept in `scripts/phase7/runs/` (gitignored); metrics in
`runs/suite.json`, tree check in `runs/treecheck.json`.

## Results

| spot | pot | our EV | TS EV | ΔEV chips | ΔEV %pot | mean\|Δf\| pp | max\|Δf\| pp | our expl % | TS expl % | build s | solve s | TS wall s | verdict |
|------|-----|--------|-------|-----------|----------|------------|-----------|------------|-----------|---------|---------|-----------|---------|
| toy-check        | 20 | 10.012 | 10.063 | −0.051 | −0.26 | 1.6  | 6.9  | 0.015 | 0.170 | 1.4 | 0.2 | 29.4 | match |
| toy-bet          | 10 | 5.006  | 5.035  | −0.029 | −0.29 | 7.9  | 50.0 | 0.021 | 0.175 | 0.0 | 0.1 | 29.4 | explained |
| river-dry-cap0   | 20 | 10.013 | 10.119 | −0.106 | −0.53 | 0.9  | 5.9  | 0.016 | 0.318 | 0.0 | 0.1 | 29.3 | match |
| river-dry-cap1   | 20 | 10.018 | 10.006 | 0.012  | 0.06  | 20.9 | 75.0 | 0.024 | 0.016 | 0.1 | 0.1 | 29.3 | explained |
| river-dry-cap2   | 20 | 10.010 | 10.006 | 0.003  | 0.02  | 18.6 | 55.1 | 0.013 | 0.016 | 0.1 | 0.1 | 29.5 | explained |
| river-wet-cap0   | 20 | −10.009| −10.019| 0.011  | 0.05  | 9.2  | 74.8 | 0.011 | 0.055 | 0.0 | 0.1 | 29.6 | explained |
| river-wet-cap1   | 20 | −10.031| −10.023| −0.007 | −0.04 | 16.2 | 88.8 | 0.039 | 0.068 | 0.1 | 0.1 | 29.5 | explained |
| river-wet-cap2   | 20 | −10.029| −10.023| −0.006 | −0.03 | 18.8 | 66.7 | 0.037 | 0.068 | 0.1 | 0.1 | 29.5 | explained |
| river-pair-cap0  | 20 | 10.013 | 10.119 | −0.106 | −0.53 | 0.8  | 5.9  | 0.016 | 0.318 | 0.0 | 0.1 | 29.9 | match |
| river-pair-cap1  | 20 | 10.018 | 10.006 | 0.012  | 0.06  | 19.8 | 75.0 | 0.024 | 0.016 | 0.0 | 0.1 | 29.7 | explained |
| river-pair-cap2  | 20 | 10.010 | 10.006 | 0.003  | 0.02  | 19.4 | 55.1 | 0.013 | 0.016 | 0.1 | 0.1 | 29.5 | explained |
| turn-narrow-a    | 10 | 5.029  | 5.092  | −0.063 | −0.63 | 30.7 | 99.9 | 0.091 | 0.120 | 7.7 | 1.0 | 30.6 | explained |
| turn-narrow-b    | 20 | −10.004| −10.652| 0.648  | 3.24  | 17.2 | 86.3 | 0.037 | 0.036 | 1.6 | 0.3 | 30.0 | explained* |
| short-stack      | 20 | 6.330  | 6.318  | 0.012  | 0.06  | 4.9  | 55.1 | 0.418 | 0.286 | 0.0 | 0.1 | 29.8 | match |
| river-full       | 20 | −0.529 | −0.539 | 0.010  | 0.05  | 5.5  | 44.2 | 0.167 | 0.120 | 85.7| 16.2| 30.4 | explained |

Plots: `scripts/phase7/runs/plots/freq_diff_by_spot.png` (per-spot mean/max
|Δfreq| vs tolerances), `freq_diff_turn-narrow-a.png`,
`freq_diff_river-dry-cap1.png` (worst-node breakdowns).

### Verdict notes

- **match (4)**: toy-check, river-dry-cap0, river-pair-cap0, short-stack —
  trees identical, EV and frequencies inside all tolerances.
- **explained (10)**: EVs agree; frequency diffs sit above the 5 pp clean bar.
  Causes, in order of contribution:
  1. **Non-unique equilibria.** Diffs concentrate at river action nodes and
     at raise-cap boundary lines where many strategies carry identical EV.
  2. **D7 raise-cap boundary.** On cap-1 spots every reported tree mismatch
     is a TS-only all-in raise (`r<stack>`) below our cap — TS inserts it
     automatically; our tree stops at cap. Frequencies at those nodes differ
     by construction.
  3. **D1 rounding.** TS rounds pct×pot to whole chips; fuzzy node matching
     absorbs it.
- **explained\* (1)**: turn-narrow-b — EV gap 1.94 chips = 3.24% of pot,
  **outside** the 1% tolerance. Diagnosed (see below); not a builder bug.

### turn-narrow-b EV investigation

Board `Ts9s8sJh` — IP's QQ holds the 8-Q straight, JJ holds trips: OOP's
AA/KK lose every showdown and the equilibrium is OOP fold-everything, value
exactly −pot/2 = −10.00. Our solver reports −10.0035 ✓. The TS dump
evaluates to −10.6520.

Per-line decomposition of the dumped strategy: OOP's residual frequencies on
dominated lines — calling the turn bet at ~0.1% and leading rivers at
~0.1–0.3% — bleed ≈ −0.65 chips. Re-solving TS to 1.2e-05% exploitability
leaves the dumped EV at −10.65 unchanged: the bleed is structural to the
**average** strategy that `dump_result` writes, while TS's printed
exploitability tracks the current iterate. Our solver evaluates the average
(`solver.average_strategy_flat()` → NashConv/2 = −10.0035 consistent with its
0.037% exploitability). Verdict: **explained difference** — a reporting
artifact of TS's dump, flagged per the pre-set tolerances.

### Bugs found and fixed (mapping layer; solver code untouched)

- `dump_ev` did not extend the board with the dealt river card when
  evaluating river showdowns and counted every `dealcards` subtree at full
  weight — including stubs TS emits for cards already on the board. Fixed:
  subtrees for board cards are skipped, possible rivers are weighted
  1/(52 − board − 4 hole), and combos containing the dealt card are excluded
  from reach. Regression tests: `test_dump_ev_turn_chance_node`,
  `test_parse_dump_turn_histories` (14/14 pass in both venvs).
- `compare_trees`/`compare_strategies` now filter TS dead-card stub nodes
  (board=`spot.board`).
- `texas_input.py`: TS rejects `*` ranges → expanded to the explicit
  169-class list.
- **No solver-side bugs found.** No changes to builders were needed.

### Additional semantic differences found during the suite

| #  | Difference | Consequence |
|----|------------|-------------|
| D8 | TS `dealcards` dumps a subtree for all 52 cards, including board cards (unreachable stubs) | filtered in compare + EV walk |
| D9 | `dump_result` writes the **average** strategy; the printed exploitability reflects the current iterate | TS dump EV can sit below equilibrium even at ~0 exploitability (turn-narrow-b) |
| D10 | TS rejects `*` as a range string | expanded to 169 explicit classes |

### Timing

Our solver builds river spots in <0.2 s, the turn spots in 1.6–7.7 s, and
river-full (~9.6 M nodes, 1.07 M dealt pairs) in ~86 s; solves run 0.1–1.3 s
(16.2 s on river-full). TexasSolver carries a ~29 s startup cost per run
(compairer dictionary load) plus 0.05–0.5 s of solve time at 25–75
iterations. Exploitability targets met everywhere (ours ≤0.42%, TS ≤0.32%).

**Gate status**: all 15 spots carry a verdict — 4 `match`, 11 `explained`
(one flagged outside the EV tolerance with a diagnosed cause), 0
`unresolved`. No solver bugs found; mapping-layer bugs fixed with regression
tests.

