# Phase 10.2 — latency, contract wiring, doc guards

Date: 2026-11-17. Status: done — gate green on the outpost.

Everything in the test-case section below was written **before**
implementation and is not revised after seeing results.

## Scope

Fix the three Phase 10.1 warnings and the `/node` latency:

1. Scale table re-measured with timings from the job status (build
   seconds and solve elapsed, plus iterations and final exploitability),
   separately from viewer steps; the Phase 10.1 `solve_s` column was
   actually end-to-end scenario wall time and is relabeled; the guard
   verifies the new table against the committed artifact.
2. `ui/scripts/check-mutation.mjs` wired into `scripts/run_all_tests.sh`,
   plus a meta-test that fails when any contract check is missing.
3. `docs/PROGRESS.md` gains Phase 10.1/10.2 entries; a guard fails when
   any `docs/PHASE*.md` has no PROGRESS.md entry linking to it.
4. `/node` latency: the strategy index is built and persisted at job
   completion, a per-job LRU cache holds loaded indexes and computed
   subtree values, and the per-combo subtree walk is vectorized (numpy).
5. The PHASE8_5 ΔEV worked example is corrected against the committed
   JSON; `check-bodies` covers every union branch of `bet_sizes` and
   `raise_sizes`.

## Test cases (fixed)

### 1 — scale table re-measure

`ui/scripts/scale-e2e.mjs` records, per spot, from the job status:
`num_nodes`, `build_seconds`, `elapsed_seconds` (solve), `iterations_run`,
`exploitability`; and per spot from HTTP calls: `tree_ms` (`/tree` at
root), first `/node` ms, warm `/node` ms (identical call repeated), and
viewer TTFR ms. The PHASE10_2 results table carries these columns.

`scripts/phase10/check_suites.py` gains `check_phase10_2`: the table must
equal `scripts/phase10/logs/scale-10-2.json` (nodes, build s, solve s,
iterations, exploitability at displayed precision; first/warm /node and
/tree at 1 ms precision), and the before/after /node comparison cells must
match `scale-10-2-before.json` (Phase 10.1 baseline) at the same
precision.

PHASE10_1.md is corrected: `solve_s` is relabeled *scenario wall time*
(submit → done + viewer steps), the "subsequent calls ms-scale" claim is
corrected (cold `/node` after the index build was 0.18–9 s, and on the
turn/raisecap spots still multi-second), and the PHASE8_5 worked example
is fixed to the artifact's actual `ev_diff` chips and pot
(`-0.006892167873383315`/`20.0` → `0.034%`, `-0.003533624896832066`/`20.0`
→ `0.018%`).

### 2 — contract checks wired + meta-test

`scripts/run_all_tests.sh` runs `node scripts/check-mutation.mjs` with the
other three ui checks. A meta-test (`tests/test_ui_contract_wiring.py`,
api venv) fails when `check-mutation.mjs`, `check-schema.sh`,
`check-no-fetch.mjs`, or `check-bodies.mjs` is absent from
`run_all_tests.sh`, and runs each check's own negative path:

- check-mutation exits non-zero when the schema regeneration step is a
  no-op (a stubbed `openapi-typescript` that copies the input unchanged):
  renaming a field without regenerating must not fail tsc → the check
  must report FAIL.
- check-no-fetch and check-bodies already embed their own negative
  assertions; the meta-test asserts they exit 0 on the real tree and
  non-zero on a deliberately broken input.

### 3 — PROGRESS.md guard

`check_suites.py` fails when a `docs/PHASE*.md` file has no PROGRESS.md
entry linking to it (`docs/PHASE10_1.md` style path mention in a phase
entry). Backfilled: Phase 10 Part 1 and 10.1 entries were missing;
10.2 is added here. The mutation test feeds the guard a tree where a
PHASE doc is unreferenced and asserts failure.

### 4 — /node performance targets

Written before measuring:

- first `/node` on each of the three scale spots ≤ **2 s**;
- warm `/node` ≤ **500 ms**;
- `/node` on the 573k river ≤ **1 s** even on the first hit after the job
  finishes.

Profile first: split `load_index` (npz parse + `TreeIndex` build),
`reach_at`, `node_action_evs` (the subtree walk), `_node_weights`,
per-combo `equity`, and response serialization on the scale jobs, and
record where the time goes in the results.

Fix (in preference order):

- the `TreeIndex` is built in `_export_strategy` at job completion and
  persisted next to the `.npz` (`strategy-<id>.idx.npz`: node/row arrays,
  not pickle);
- `_strategy_index` keeps a per-job LRU cache of loaded indexes
  (bounded, `HOLDEM_INDEX_CACHE_JOBS`, default 4) and a per-(job,
  history, combos) cache of computed `node_values` payloads
  (bounded, `HOLDEM_NODE_VALUES_CACHE`, default 256);
- `node_action_evs` gets a vectorized numpy implementation used by
  `/node` (the pure-Python `evwalk` stays the shared reference).

No-change invariants:

- `scripts/phase9/validate_node_evs.py` re-run on all 6 spots: same
  comparison set and `max |Δ|` ≤ 1e-12;
- `scripts/phase9/brute_ev.py` re-run green;
- a new test asserts the vectorized evaluator equals
  `evwalk.node_action_evs` per combo per action on synthetic trees
  (both venvs where numpy is present);
- `tests/test_evwalk.py` property tests pass unchanged.

Memory: the doc records the index cache's measured RSS per entry and the
node_values cache size; both are bounded by the configured caps.

### 5 — PHASE8_5 worked example + union-branch fixtures

`check-bodies.mjs` carries one fixture per union branch of
`SolveRequest.bet_sizes` (preset literal, number list, per-player map,
per-street map) and `raise_sizes` (`["x", m]` and `["p", f]`), each
validated against the schema. A test fails when a union branch has no
fixture: the fixtures are checked against every `anyOf` branch schema and
each branch must validate ≥ 1 fixture.

### 6 — re-run evidence

`npm run e2e:real` and the scale run after the fix; the doc records
before (scale-10-1.json / scale-10-2-before run) and after.

## Results

### 1 — scale table re-measured

Outpost systemd API on :8000, two RTX 5060 Ti workers, f32.
Committed artifact: `scripts/phase10/logs/scale-10-2.json`
(produced by `ui/scripts/scale-e2e.mjs`). `build s` and `solve s` come
from the job status (`build_seconds`, `elapsed_seconds`) — *not* the
viewer; `first /node` is a cold call measured before any `/tree`
touches the job, `warm /node` the identical call repeated.

| spot | nodes | build s | solve s | export s | iters | expl (% pot) | stop | first /node ms | warm /node ms | /tree ms | TTFR ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| scale-river-573k | 573,031 | 5.9 | 8.1 | 0.23 | 200 | 0.400 | iteration cap | 64 | 3 | 5 | 253 |
| scale-turn-chance | 442,891 | 6.1 | 24.6 | 16.44 | 200 | 1.271 | iteration cap | 168 | 3 | 3 | 94 |
| scale-raisecap2 | 95,743 | 1.1 | 2.7 | 0.63 | 200 | 0.577 | iteration cap | 23 | 3 | 3 | 93 |

UI errors: 0 on all three spots. `/node` still returns 422 on chance
histories by design (`check/check` on the turn spot).

Before/after `/node` at root (Phase 10.1 baseline `scale-10-1.json`
→ this run):

| spot | first /node before | first /node after |
| --- | ---: | ---: |
| scale-river-573k | 39,246 ms | 64 ms |
| scale-turn-chance | 32,309 ms | 168 ms |
| scale-raisecap2 | 14,916 ms | 23 ms |

The `before` baseline was measured by a different method — its cold
`/node` ran *after* `/tree` calls on the job (warm index path), so the
two columns are not like-for-like. Re-measured once with the same
method as `after` (cold `/node` at root on the persisted strategy,
no `/tree` first): river 38.42 s, turn 7.98 s, raise-cap-2 3.29 s —
still two orders of magnitude slower than the warm-index path.

All targets met: first `/node` ≤ 2 s on all three spots (64 / 168 /
23 ms), warm `/node` ≤ 500 ms (≤ 3 ms), and the 573k river is 64 ms on
the first hit after the job finishes (≤ 1 s).

`check_phase10_2` verifies the table above against the artifact.
PHASE10_1.md corrected: the `solve (s)` column relabeled *scenario
wall (s)* with a definition note; the "later calls ~0.2–9 s" claim
corrected — warm-index calls on the turn and raise-cap spots were
still 8–19 s — and the PHASE8_5 worked example fixed to the artifact's
chips (−0.006892/20.0 = −0.03446% → |ΔEV| 0.034%, −0.003534/20.0 =
−0.01767% → 0.018%).

### 2 — contract checks wired + meta-test

`run_all_tests.sh` now runs `node scripts/check-mutation.mjs` with
check-bodies, check-no-fetch and check-schema.
`tests/test_doc_evidence.py` gains:

- `test_run_all_tests_wires_all_contract_checks` — fails when any of
  the four checks is absent from `run_all_tests.sh`;
- `test_check_mutation_fails_when_schema_regen_is_noop` — stubs `npx`
  so `openapi-typescript` exits 0 without rewriting `schema.d.ts`;
  `check-mutation.mjs` must exit 1 (verified: it reports "FAIL: tsc
  passed with a renamed request field").

check-no-fetch and check-bodies keep their own embedded negative
assertions.

### 3 — PROGRESS.md guard

`test_every_phase_doc_linked_in_progress` (test_doc_evidence.py) fails
when any `docs/PHASE*.md` basename is absent from `docs/PROGRESS.md`.
Backfilled: Phase 10 Part 1, 10.1, and 10.2 entries added (the Part 1
entry existed but lacked a `PHASE10.md` link — it names the doc now).

### 4 — /node performance

**Profile (before):** a first `/node` spent its time in three parts —
`.npz` parse + `TreeIndex` build (~1–3 s), then `node_action_evs`
walking the subtree once per hand combo (~1326 combos × subtree DFS —
the dominant term: river root 38.3 s of the 39.2 s total, turn root
32.3 s, raisecap2 root 14.9 s), plus ms-scale serialization. Warm hits
dropped only the index-rebuild part: the per-combo walk still cost
0.2–19 s. Per-river-card fan-out on the turn spot was part of the
walk's cost, not a separate overhead.

**Fix.** `api/evec.py` is a vectorized evaluator: a single DFS over
tree nodes carrying a (1326,) opponent-reach vector; showdown values
come from rank-sorted prefix sums with per-card cumsum corrections,
chance edges apply dealt-card masks, and per-own-node `record` callbacks
collect action EVs — O(tree nodes × combos) vectorized instead of
per-combo DFS. `nodes.export_node_evs` runs `all_node_evs` once at job
export (in `_export_strategy`) and persists
`strategy-<id>.evs.npz` (flat `ev` + `opp_mass` arrays aligned with the
strategy `values`/`keys`, plus a `keys` copy for staleness detection).
Export cost on the scale spots: 0.19 s river, 15.9 s turn-chance
(16,668 keys), 0.56 s raisecap2 — paid once in the job's
`elapsed_seconds` tail instead of per request. `/node` with a persisted
file gathers the stored EVs (`load_node_evs` returns None on a
missing/corrupt/stale file and falls back to the live vectorized walk).
`_strategy_index` LRU-caches 4 loaded indexes
(`HOLDEM_INDEX_CACHE_JOBS`) including their evs; `get_node` LRU-caches
256 (job, history) payloads (`HOLDEM_NODE_VALUES_CACHE`).

**No-change invariants:** `validate_node_evs.py` re-run — all six spots
within tolerance, `max |Δ|` 7.99e-15 (artifact
`scripts/phase9/runs/ev_validation_*.json` regenerated);
`brute_ev.py` — both comparisons pass, worst |Δ| 1.78e-14. `tests/test_api_evec.py` —
5 passed: per-node equivalence vs `evwalk.node_action_evs` at multiple
histories, synthetic chance/fold index, `all_node_evs` full-node
coverage, `load_node_evs` roundtrip + stale-key rejection, persisted vs
computed `node_values` identical. `tests/test_evwalk.py` unchanged and
green.

**Memory:** loading index + evs for all three scale jobs raised RSS
from 10 MiB to 74 MiB (+51 MiB on the first load including the numpy
import, +13 MiB turn, ≈0 raisecap2); evs arrays on disk are 0.08 / 1.33
/ 0.09 MiB vs the strategy files' 0.07 / 1.20 / 0.08 MiB. Bounds:
`HOLDEM_INDEX_CACHE_JOBS=4` caps cached jobs (~60 MiB on this
workload), `HOLDEM_NODE_VALUES_CACHE=256` caps payloads (≤ ~5 MiB at
21 KiB each).

### 5 — PHASE8_5 example + union fixtures

`check-bodies.mjs` now builds one fixture per `bet_sizes` union branch
(preset set, number list, per-player `{"0","1"}`, per-street
`{turn,river}`) and both `raise_sizes` kinds (`x`, `p`), validates each
against `openapi.json`, and then classifies every `anyOf` branch — a
branch with no fixture fails the check (verified: removing the
per-player/per-street fixtures reports "no fixture exercises bet_sizes
union branch players/streets").

### 6 — re-run evidence

- `npm run e2e:real` → **PASS** (7 asserted steps; screenshots 01–07
  regenerated in `scripts/phase10/logs/`).
- `npx playwright test` → **10 passed** (30.3 s; a11y + e2e vs fake
  engine).
- Scale run: the table above; all targets met, 0 UI errors.

## Gate

`scripts/run_all_tests.sh` green on the outpost including the new
checks (check-mutation wired, wiring meta-test, PROGRESS guard, evec
tests); Playwright suites green (10 passed); all `/node` performance
targets met; `check_suites.py` passes — docs match committed
artifacts (`scale-10-2.json` verified by `check_phase10_2`).
