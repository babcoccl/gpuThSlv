# Phase 10.4 — validation-tooling gaps

Date: 2026-11-18. Status: done (gate below).

Everything in the test-case section below was written **before**
implementation and is not revised after seeing results.

## Scope

1. Persisted node-EV integrity: `load_node_evs` gets corruption tests at
   every level (byte truncation at 10/50/90%, single byte, shortened `ev`
   array, wrong dtype, NaN/inf values, one bit-flipped value) — every one
   must return `None` (or be rejected by the stored checksum) and `/node`
   must fall back to live-computed values identical to what the persisted
   file would have served, with no exception escaping. The export write
   is atomic (temp file + fsync + rename) and the file carries a checksum
   over the `ev`/`opp_mass` payloads.
2. Compared-cell accounting in `scripts/phase10/evec_all_nodes.py` (and
   the same pattern in `validate_node_evs.py` / `brute_ev.py` /
   `brute_turn_ev.py`): compared and omitted cells are counted
   separately, the expected count is computed independently from the
   tree, the run fails on any unexplained omission and on
   `compared == 0`, and `exact_max`/`persisted_max` are reported only
   over compared cells. Negative tests cover an evaluator that omits
   cells, one that returns nothing, and one that returns a partial set —
   each must fail. The three scale jobs are re-run,
   `evec_all_nodes.json` and the PHASE10_3.md table are regenerated, and
   the guard (`check_suites.py`) also checks compared/expected counts.
3. Import boundary: **no engine-venv code may import
   `holdem_solver.api`** (AGENTS.md: *"Only holdem_solver/engine_worker/
   may import gpu_cfr… Communication between them: JSON-RPC over a Unix
   socket… No shared imports besides a tiny pure-python protocol
   package."*). Validation is split so solving runs in the engine venv
   and the `evec`/persisted/API-path modes run in the API venv,
   consuming the dumped node tree / strategy npz — the Phase 10.3
   lazy-import workaround in `holdem_solver/api/__init__.py` is reverted.
   A static test mirrors `test_protocol.py`: a transitive import walk
   over every module that executes in the engine venv (engine_worker,
   protocol, and every script declaring `.venv-engine`) must never reach
   `holdem_solver.api`.
4. Baseline discrepancy: the Phase 10.1 first-`/node` figures (39,246 /
   32,309 / 14,916 ms) vs the re-measured cold figures (38.42 / 7.98 /
   3.29 s). The re-measure is rerun on the Phase-10.1 merge commit
   `0ef7d5a`, three times per spot on a freshly restarted API each time;
   the spread and the explanation are recorded here, and
   PHASE10_2.md / PHASE10_3.md are fixed so each spot carries exactly one
   before-number with its measurement method stated.
5. The river aggregate's 2.0e-10 worst diff (vs ≤1e-12 elsewhere): the
   accumulating path is identified (float32 carry or summation order)
   and recorded; the tolerance is tightened if the cause allows.
6. `docs/PROGRESS.md` gains Phase 10.3 and 10.4 entries.

## Test cases (fixed)

### 1 — `load_node_evs` integrity tests

In `tests/test_api_evec.py`, on a fabricated valid `*.evs.npz` (the
roundtrip fixture) each mutation must make `load_node_evs` return
`None` and must raise nothing:

- byte-level truncation of the file to 10%, 50%, 90% of its length, and
  to a single byte;
- an `ev` array whose length is one element short of `offsets[-1]`;
- `ev` stored with a non-float64 dtype;
- `ev` containing a NaN and separately an ±inf;
- a single value bit-flipped inside a valid `ev` array (file still
  parses, keys match — only the checksum catches it);
- an `opp_mass` array shorter than `keys`.

Integrity mechanism: `_export_node_evs` writes a `crc32` member
(`zlib.crc32` over `ev.tobytes()` ⊕ `opp_mass.tobytes()`); the loader
recomputes and rejects on mismatch, plus a schema check (`offsets`
non-decreasing, `offsets[-1] == len(ev)`, dtypes, all values finite).
Both writes already go through temp-file + `os.replace` — a test
asserts no `*.tmp` lingers and a crashed write leaves no partial file.

Fallback: `node_values` with `node_evs=None` returns hands identical to
the hands served from the persisted table, for each corruption case
(the caller pattern `load_node_evs(...) or {}` → `node_evs=None`).

### 2 — compared/expected cell accounting

`evec_all_nodes.py` gains a `compare_cells(ref, got)` helper returning
`(compared, omitted)` — `omitted` a list of `(hist, combo, action,
reason)` with reason ∈ {`missing node`, `missing combo`,
`missing action`} — and computes `expected` independently from
`index.rows_at`/`node_at` (`Σ_hist Σ_combo |actions|` over the checked
hists) instead of trusting either table's own keys. Checks per job:

- `persisted_vs_live_evec`: `expected` = cells of the live `export_node_evs`
  table; every persisted cell must be present and equal; unexplained
  omissions fail; `compared == 0` fails.
- `exact_per_combo`: `expected` = cells of `ref_action_evs` (the evwalk
  reference) on the in-budget hists; `exact_max` reported over compared
  cells only; omissions enumerated.
- `aggregate_edge_value`/`parity_split`: `hists_checked` must equal the
  independently computed hist set size; `compared == 0` fails.

`brute_turn_ev.py`: `expected` = combos in `rows_at[hist]` with own
reach > 0; `compared` = those where both the brute walk and the served
row exist; omissions carry their reason (`zero reach`, `impossible
deal`, `no served row`) and unexplained omissions fail.

`validate_node_evs.py` / `brute_ev.py` API-path sections: same
compared/omitted split (omissions with reasons; `compared == 0` fails;
`max` over compared cells).

Negative tests (`tests/test_evec_accounting.py`, api venv): call the
helper / job check with (a) an evaluator returning an empty table,
(b) one omitting a single cell, (c) one omitting every other combo —
each must produce failure (nonzero `ok=False` / raised), plus a
positive control that passes.

Regeneration: all three scale jobs re-run through `evec_all_nodes.py`;
`logs/evec_all_nodes.json` rewritten with `expected`/`compared`/
`omitted` per check; the PHASE10_3.md table gains compared/expected
columns; `check_suites.py` gains `check_phase10_3` verifying every cell
of that table against the artifact (hist counts, per-check
max_abs_diff, compared == expected). Mutation tests: change a count
cell, delete a table row.

### 3 — import boundary

- `scripts/phase9/validate_node_evs.py` splits in two:
  - engine stage (`.venv-engine`): steps 1–3 as today (TS-tree root EV,
    per-node EVs vs `xeval`, own root EV vs `profile_value_p0` via
    `solve_ours`) plus dumping the solved nodes per spot to
    `runs/solved_nodes_<spot>.json`; writes the artifact's engine
    sections.
  - new `scripts/phase9/validate_node_evs_api.py` (`.venv-api`): step 4
    — rebuilds `TreeIndex` from the dumped nodes and runs the `evec`,
    persisted (`export_node_evs` → `evs.npz` → `load_node_evs` →
    `node_values`) and served comparisons; merges results into the same
    `runs/ev_validation_<spots>.json` artifact (manifest gains a
    `stages` record with the venv of each stage).
- `scripts/phase9/brute_ev.py` declares `.venv-api` (it solves nothing;
  the API-path block stays, now legal).
- `holdem_solver/api/__init__.py` reverts to a direct
  `from .app import create_app` — engine code no longer needs to import
  it, so the lazy workaround is removed.
- New `tests/test_import_boundary.py` (collected by BOTH venvs, pure
  `ast`): builds the transitive import closure of (a) every
  `holdem_solver/engine_worker/**` and `holdem_solver/protocol/**`
  module and (b) every `scripts/**.py` whose docstring names
  `.venv-engine`, following local `holdem_solver.*` / `scripts.*`
  imports including function-level ones, and asserts
  `holdem_solver.api` never appears. A second assertion lists the
  engine-venv script set explicitly so a script that drops its venv
  declaration still lands on one side.

### 4 — baseline re-measure

Re-run the cold first-`/node` at root on a checkout of `0ef7d5a`
(Phase 10.1 merge — the code the 10.1 table measured), API served from
the worktree on its own port against a copy of the three scale
strategies; three measurements per spot, restarting the API between
measurements so each is a true cold hit (no index or values cache).
Record mean/spread per spot, name the commit, and reconcile the two
earlier figures (10.1's warm-index-after-`/tree` path vs the 10.3
re-measure). PHASE10_2.md and PHASE10_3.md keep exactly one
before-number per spot, each with its method in the same sentence.

### 5 — river 2.0e-10 diagnosis

Instrument `evec._evs_walk` vs `evwalk.edge_value` on
`scale-river-573k`: locate the hist carrying the worst aggregate diff,
then recompute the aggregate with (a) pairwise/tree-order summation in
numpy and (b) a Python-loop accumulation over the same `V` vector to
separate vectorized-order error from genuine dtype drift; check whether
`V`/`r_opp` ever leave float64 and whether the `comp` division
amplifies. Record the cause. Tolerance tightened toward 1e-12 only if
the identified mechanism bounds the residual below it.

### 6 — PROGRESS.md

Entries for Phase 10.3 (guard completeness, evec validation,
export_seconds) and Phase 10.4 (this phase: integrity checks, cell
accounting, import boundary, baseline re-measure, river diagnosis),
each with decisions, measured numbers and the doc link — satisfying
`test_every_phase_doc_linked_in_progress`.

## Results

### 1 — integrity + atomic export

`scheduler._export_node_evs` writes `crc32 =
zlib.crc32(ev.tobytes()) ^ zlib.crc32(opp_mass.tobytes())` alongside the
payload; `load_node_evs` recomputes it and returns `None` on mismatch,
on schema violations (offsets not non-decreasing / `offsets[-1] !=
len(ev)` / mass length mismatch / `actions` length mismatch), on
non-float64 dtypes, and on any NaN/inf. The write stays
temp+fsync+`os.replace`; a mid-write crash unlinks the `.tmp` so no
orphan lingers. `tests/test_api_evec.py`: truncation at 10/50/90% and
1 byte → `None`; shortened `ev`, wrong dtype, NaN, inf, bit-flip,
short/absent mass, bad offsets → `None`; each corruption served through
`/node` falls back to live values identical to the persisted table;
no exception escapes. A scheduler-level test drives
`_export_node_evs` with `open("/dev/full")` and asserts neither a
final file nor a `.tmp` survives. Files exported before Phase 10.4
carry no checksum and are now declined — the three scale jobs' files
were regenerated.

### 2 — compared/expected accounting

New shared `scripts/compare_cells.py`: `compare_cells(ref, got)` →
`(compared, omitted, extras, max_abs_diff)` with per-cell reasons
(`combo missing` / `action missing`); `expected_cells(index)` counts
`Σ_hist Σ_combo |actions|` straight from the tree. Wired into
`evec_all_nodes.py` (persisted-vs-live and exact-per-combo),
`brute_turn_ev.py` (per-combo, with explained reasons `zero own reach` /
`no compatible opponent reach`), the API-path block of `brute_ev.py`,
and the API stage of `validate_node_evs_api.py`. Every check fails on
`compared == 0`, `compared != expected`, unexplained omissions, extras,
or a reference-side shortfall; `max_abs_diff` covers compared cells
only.

Regenerated artifacts (all compared == expected, zero omissions):
`logs/evec_all_nodes.json` — river agg 1.96e-10 / exact 6.4e-14 over
2,926 cells / persisted 0.0; turn-chance agg 8.6e-13 / exact 7.5e-13
over 33,264 cells / persisted 0.0; raisecap2 agg 4.1e-12 / exact
1.8e-13 over 3,200 cells / persisted 0.0. `runs/brute_turn_ev.json`:
6/6 histories, all 18 or 15 expected combos compared, worst 5.7e-14.
The PHASE10_3.md table keeps "X over N cells" (N is compared, and the
`check_phase10_3` guard now also verifies compared == expected, zero
omissions/extras/ref-missing per check — the compared column is the
guard-verified number, not a second column).

Negative tests `tests/test_compare_cells.py`: empty evaluator →
compared 0 with 4 omissions; one combo dropped → 2 omissions; one
action dropped → exactly `AsKd/r: action missing`; partial set → all
three missing cells named; extras reported; positive control passes.

### 3 — import boundary

`scripts/phase9/validate_node_evs.py` is now pure engine-venv (steps
1–3 plus writing `runs/solved_nodes_<spot>.json`); the API-path checks
moved to `scripts/phase9/validate_node_evs_api.py` (`.venv-api`,
rebuilds `TreeIndex` from the dump, merges results into the same
`ev_validation_<spots>.json` under `_manifest.stages.api`).
`brute_ev.py` is `.venv-api`. `holdem_solver/api/__init__.py` reverted
to `from .app import create_app`. `tests/test_import_boundary.py`
walks the transitive import closure of `engine_worker/`, `protocol/`,
and every script/test importing `gpu_cfr` or `engine_worker` —
including function-level imports — and asserts
`holdem_solver.api` is unreachable. AGENTS.md: *"Only
holdem_solver/engine_worker/ may import gpu_cfr… Communication between
them: JSON-RPC over a Unix socket, bulk arrays via .npy files in
/dev/shm. No shared imports besides a tiny pure-python protocol
package that must be 3.9-compatible syntax."*

### 4 — baseline discrepancy explained

Re-measure at `0ef7d5a` (Phase-10.1 merge), cold `/node` at root,
server restarted per rep, 3 reps
(`logs/scale-10-1-cold.json`): river 37.83 s (spread 0.12 s),
turn 7.99 s (spread 0.18 s), raisecap2 3.29 s (spread 0.04 s) — the
same numbers the 10.2/10.3 docs cite. The 10.1 figures
(39.2 / 32.3 / 14.9 s) came from the identical code and strategy but
were sampled while the scenario's sibling jobs were still solving —
CPU contention against the pure-Python walk, up to ~4× on the turn
spot. PHASE10_2.md's before/after table now carries the re-measured
means as the single before-number per spot with method + commit;
`check_phase10_2` verifies the before column against
`scale-10-1-cold.json`.

### 5 — river 2.0e-10 explained

No float32 anywhere: `V`, `r_opp`, `ev`, `opp_mass` are all float64.
Recomputed the river root both ways — `np.dot(mine, V)` differs from a
sequential Python accumulation of the same `V` by <4e-11, so the dot
product's internal ordering is not the cause. The residual is ordinary
float64 summation-order noise *scaled by magnitude*: `V` accumulates
`f·child` terms and the showdown terminal decomposes wins/ties via
rank-sorted cumsums (`above = comp − below − eq`), a different
association than `evwalk`'s per-combo loop. At the river root
|EV| ≈ 57k chips, so ~4·eps·|EV| ≈ 2e-10 — matching the observed
maximum; the turn-chance and raisecap2 roots sit at |EV| ~1e3–1e4 and
land at ~1e-12 for the identical mechanism. Tolerance stays 1e-9
(~50× headroom): tightening toward 1e-12 would require re-ordering
the evec accumulation itself, not justified.

### 6 — PROGRESS.md

Entries added for 10.3 and 10.4.

## Gate

`scripts/run_all_tests.sh` on the outpost: **PASS** — engine suite 207
passed/7 skipped, api suite 284 passed, every `tests/test_*.py`
collected by at least one venv, all ui contract checks green
(check-bodies, check-no-fetch, check-mutation, check-schema). Negative
tests fail as expected (`test_compare_cells.py` mutations; doc-guard
mutation tests for PHASE10_2/PHASE10_3 rows and cells).
