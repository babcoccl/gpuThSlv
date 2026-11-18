# Phase 10.3 — guard completeness and evec validation

Date: 2026-11-18. Status: done.

Everything in the test-case section below was written **before**
implementation and is not revised after seeing results.

## Scope

1. `check_phase10_2` hardening: derive the expected spot set and order
   from `scale-10-2.json` and require equality with the main table — no
   missing, extra, or reordered rows. Verify every `before`/`after` cell
   of the /node comparison table against the artifact it names. The
   doc's test-case section named `scale-10-2-before.json` while the
   committed baseline artifact is `scale-10-1.json` (Phase 10.1's own
   artifact, cited in PHASE10_1.md); resolved to `scale-10-1.json` —
   that is the name the results table already uses and it stays
   committed.
2. Audit every table guard — `check_suites.py` for the PHASE7, PHASE8,
   PHASE8_5, PHASE8_6,
   VALIDATION, PHASE10 — plus `check_evidence.py`'s per-spot count lists
   with a row-deletion and a row-addition mutation; any guard that still
   passes is fixed. The completeness check lives in one shared helper
   used by every table check.
3. The vectorized evaluator (`api/evec.py`) is validated:
   (a) `validate_node_evs.py` and `brute_ev.py` gain an API-path mode —
   EVs computed via `evec` directly and via the persisted
   `strategy-<id>.evs.npz` exactly as `/node` serves them — re-run on
   all six cap-0 river spots, the worst absolute delta reported per
   path.
   (b) `evec.node_action_evs` is compared against
   `evwalk.node_action_evs` on EVERY player node of the 573k river,
   turn-chance (including histories after `river:` chance edges), and
   raise-cap-2 scale jobs at tolerance 1e-9, plus subtree-value equality
   at every chance position.
   (c) an independent brute-force EV check on a small turn spot:
   enumerate every river card, showdown-evaluate from scratch, compare
   against `/node` values at several histories including after a chance
   edge.
   (d) persisted-vs-live `node_values` for stale, corrupt, and missing
   `evs.npz` files. If any check fails, `evec` is fixed, not the
   tolerance.
4. `export_seconds` on `SolveStatus` (separate from solve elapsed),
   OpenAPI schema/client regenerated, an `exporting` state shown in the
   UI jobs list and viewer, and the scale table updated: exploitability
   carries units and a stop reason.
5. The doc records that the Phase 10.1 baseline measured `first /node`
   after `/tree` calls had already warmed the index (a different method
   than 10.2's cold-first call), and re-measures the before figure once
   with the same method where practical.

## Test cases (fixed)

### 1 — check_phase10_2 completeness + mutation tests

`check_phase10_2` builds the expected spot list IN ORDER from
`scale-10-2.json` and fails when the main table's first column differs
in set or order (missing row, extra row, reordered rows). The
before/after table must carry exactly the same spot set: `before`
checked against `scale-10-1.json` `calls[0].node_ms`, `after` against
`scale-10-2.json` `calls[0].first_node_ms`, both at displayed precision.

Mutation tests (`tests/test_doc_evidence.py`, `PHASE10_2.md` copies):
each of — change a main-table cell, change a `before` cell, change an
`after` cell, delete a main-table row, delete a before/after row, add a
bogus row — must exit 1.

### 2 — shared completeness helper + audit

One helper in `check_suites.py`:
`check_rows(doc, rows, expected_keys, what, key=...)` fails on any
missing, extra, or (where the artifact defines an order) reordered row.
Every spot-keyed table check calls it: PHASE7 results, PHASE8 main/
xeval/xeval-phase7/EV-gap tables, PHASE8_5 float32 + xeval tables,
PHASE8_6, VALIDATION phase rows, PHASE10 jobs, PHASE10_2 main +
before/after. `check_evidence.py`: a slash-separated per-spot count
list beside the ev_validation citation fails when its length differs
from the artifact's spot count (deletion or addition can no longer slip
through as "not a claim").

Mutation tests: row-deletion and row-addition per table (at minimum one
per guard: PHASE7, PHASE8 main, PHASE8 xeval, PHASE8 EV gap, PHASE8_5,
PHASE8_6, VALIDATION, PHASE10, PHASE10_2 main + before/after, and the
PHASE9 slash list) each exit 1.

### 3 — evec validation

(a) `validate_node_evs.py` gets `--via {evwalk,evec,persisted,all}`;
`evec` mode calls `evec.node_action_evs`, `persisted` mode writes
`export_node_evs` output to a temp `evs.npz` and reads the served values
back through `load_node_evs` + `node_values`. Re-run on all 6 spots;
the doc reports `max |Δ|` per path (`evec` and `persisted` vs the
`evwalk`/`xeval` references). `brute_ev.py` gains the same API-path
comparisons on its two spots.
(`holdem_solver/api/__init__.py` becomes importable without fastapi —
lazy `create_app` — so the engine venv can import `api.evec`/`api.nodes`;
numpy already exists in both venvs.)

(b) `scripts/phase10/evec_all_nodes.py` (api venv): loads each scale
job's committed strategy npz from the API data dir, and for every player
node in `index.node_at` compares `evec.node_action_evs` to
`evwalk.node_action_evs` per combo per action at `max |Δ| <= 1e-9`
(or `|Δ| <= 1e-12 * max(1, |ref|)` — Phase 10.5); at
every chance position it compares the summed subtree value from
`_evs_walk` (aggregated over own reach) against `evwalk.edge_value`.
Runs on `scale-river-573k` (573,031 nodes), `scale-turn-chance`
(442,891, includes `river:` histories), and `scale-raisecap2` (95,743).
Writes `scripts/phase10/logs/evec_all_nodes.json`.

(c) `brute_ev.py` gains an API-tree mode: a `strategy-<id>.npz` is
walked with its own card parsing, own showdown evaluator and own
pairwise reach products — enumerating every river card at chance nodes —
and compared against the `/node` `node_values` EVs at root plus several
histories including a `river:<card>` history, on the turn spot.

(d) `tests/test_api_evec.py` adds: `load_node_evs` on a corrupt file
(truncated/garbage npz → None → live compute fallback), stale keys →
None, missing file → None, and `node_values` persisted-vs-live equality
on the served rows.

### 4 — export_seconds + exporting state

`SolveStatus` gains `export_seconds: float | None` (persisted jobs
column `export_seconds REAL`). The scheduler transitions the job
`solving -> exporting` before `_export_strategy` and records the wall
time the export (strategy npz + node-EV npz) took. `status` gains the
`exporting` literal everywhere it is enumerated (models, app status
filter, store ACTIVE/RUNNING sets). `ui/scripts/gen-api.sh` regenerates
`openapi.json` + `schema.d.ts`; Jobs list and the viewer show the
`exporting` badge/spinner state. `scale-e2e.mjs` records
`export_seconds` and `stop_reason` per spot; the scale table column is
`expl (% pot)` and carries a `stop` reason column, verified by the
guard against `scale-10-2.json` regenerated with the new fields.

### 5 — baseline-method caveat

Recorded in Results: the Phase 10.1 baseline's first `/node` was
measured after `/tree` calls (index cache warm path differs from the
cold-first method). The before figure is re-measured once under the
10.2 method if practical — i.e. replaying the cold `/node` on a build
without the persisted-EV/index fast path is noted honestly or
re-measured.

## Results

### 1–2 — completeness and audit

`check_rows` lives in `scripts/phase10/check_suites.py` and is called
by every spot-keyed table check. `check_phase10_2` derives the spot
list and order from `scripts/phase10/logs/scale-10-2.json`; the
before/after table reads `scale-10-1.json` `calls[0].node_ms` and
`scale-10-2.json` `calls[0].first_node_ms`. All mutation tests in
`tests/test_doc_evidence.py` exit 1 as required (changed cells, row
deletions, bogus rows, slash-list edits).

### 3 — evec validation

(a) `validate_node_evs.py --via all` re-ran on all six cap-0 river
spots (`scripts/phase9/runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`).
Per path, the worst absolute delta vs the `evwalk`/`ts` reference:
live `evec.node_action_evs` 3.9e-14, persisted `evs.npz` values 3.9e-14
(both on w-blocker-cap0). `brute_ev.py` carries the same API-path
fields on its two spots (`scripts/phase9/runs/brute_ev.json`): worst
5.3e-15 for either path.

(b) `evec_all_nodes.py` (`scripts/phase10/logs/evec_all_nodes.json`)
compared `evec` to `evwalk` at tolerance 1e-9 on every history of all
three scale jobs, including chance positions and post-`river:`-edge
histories:

| job | histories | agg worst abs diff | parity split | per-combo exact | persisted vs live |
| --- | ---: | ---: | ---: | ---: | ---: |
| scale-river-573k | 15 | 2.0e-10 | 1.4e-10 | 6.4e-14 over 2,926 cells | 0.0 |
| scale-turn-chance | 2,175 | 8.6e-13 | 4.0e-13 | 7.5e-13 over 33,264 cells | 0.0 |
| scale-raisecap2 | 81 | 4.1e-12 | 9.7e-13 | 1.8e-13 over 3,200 cells | 0.0 |

The river aggregate's worst-absolute diff is 2.0e-10 summation-order
noise (PHASE10_4 §5) on the root own-1 cell (|ref| ≈ 2.0e4, rel
≈ 9.9e-15); the worst-relative diff is 2.3e-14 on a different cell
(`check,b20,fold`). Since Phase 10.6 both are held to the single
per-cell rule `|diff| <= 1e-12 * max(1, |ref|)` and reported
separately as `worst_abs_cell` / `worst_rel_cell`. The per-combo exact check covers
nodes with at most 400 descendants
(964 of 966 turn nodes; the two skipped roots are still covered by the
aggregate and parity-split checks). Persisted `evs.npz` equals the
live walk bit-for-bit on every player node.

(c) `brute_turn_ev.py` (`scripts/phase10/runs/brute_turn_ev.json`):
independent enumeration of every river card at the chance edge with a
from-scratch showdown evaluator, on six histories of the turn spot
including three behind a `river:As` edge — worst absolute delta
5.7e-14.

(d) `tests/test_api_evec.py`: corrupt and truncated `evs.npz` files
yield `load_node_evs → None` and `/node` falls back to live `evec`
(identical values); stale keys and missing files do the same.

### 4 — export_seconds / exporting

`export_seconds` is persisted per job and surfaced on `SolveStatus`;
the UI shows an `exporting` badge; `openapi.json`/`schema.d.ts`
regenerated; `scale-10-2.json` re-measured and the PHASE10_2 scale
table now carries `export s`, `expl (% pot)` and a `stop` column (all
three runs stopped at the iteration cap).

### 5 — baseline-method caveat

Recorded in `docs/PHASE10_2.md`: the Phase 10.1 run's first-`/node`
figures (39.2 / 32.3 / 14.9 s) were sampled while sibling jobs still
ran — CPU contention, not a cold measurement. The canonical
before-numbers are the re-measure at the Phase-10.1 merge commit
`0ef7d5a` (`logs/scale-10-1-cold.json`, API restarted per rep, 3 reps,
no `/tree` first): river 37.83 ± 0.06 s, turn 7.99 ± 0.09 s,
raise-cap-2 3.29 ± 0.02 s.

## Gate

**Gate result:** `scripts/run_all_tests.sh` clean on the outpost
(engine 197 passed / 5 skipped, api 268 passed, all UI contract
checks); `npx playwright test` 10/10; `npm run e2e:real` PASS; all new
mutation tests exit nonzero as required; validation results for both
EV paths recorded above.
