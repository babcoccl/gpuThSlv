# Phase 10 — Part 0: green gate, SSE race fix, stream evidence, doc-status + suite guards

Date: 2026-11-17. Status: complete.

Everything in the test-case section below was written **before**
implementation and is not revised after seeing results.

## Test cases (fixed)

### 0a — green suite on the outpost

`scripts/run_all_tests.sh` must exit 0 on the gpu-5060ti outpost: engine
suite (`.venv-engine`, Python 3.9), API suite (`.venv-api`, Python 3.15)
including GPU-marked tests, and the collection-coverage check. Every skip
in the `-rs` output is listed with its reason (see "Skips" below); none are
unexplained.

### 0b — SSE race

1. `test_sse_finished_and_cancelled_jobs` passes **50 consecutive runs** of
   `pytest tests/test_api_sse.py::test_sse_finished_and_cancelled_jobs`
   (`.venv-api`).
2. Root cause (fixed in the server, not the test): `store.cancel()` on a
   running job sets `cancel_requested=1` but leaves `status` in a RUNNING
   state until the runner's `finish()` lands. An SSE client connecting in
   that window got a `snapshot` with `status: "solving"` while the client
   had just seen `DELETE` return `cancelled`. `_sse_snapshot` now reports
   the **effective** status — `cancelled` when `cancel_requested` is set —
   so a connecting client can never observe a stale active status after a
   cancel it issued.
3. `test_sse_evicted_id_gets_snapshot_no_replay` is deterministic: the job
   is a store-driven job (`gpus=[]`, no worker) whose progress events are
   emitted by explicit `store.update()` calls read in lockstep off the
   stream — no sleeps that depend on wall-clock timing, no real solve.
4. The whole file is run **20 times**: `pytest tests/test_api_sse.py`
   (`.venv-api`); any failure is reported under "SSE runs" below.

### 0c — real-GPU SSE stream evidence

One real engine, real GPU solve exercised over curl; transcripts committed
under `scripts/phase10/logs/`:

- `stream_running.log` — stream of a running solve (snapshot → progress);
- `reconnect_valid_id.log` — mid-stream reconnect with a valid
  `Last-Event-ID` (replay then live);
- `reconnect_stale_id.log` — reconnect with a stale/unknown id (snapshot,
  no replay);
- `finished_job.log` — stream of a finished job (snapshot → `done`).

### 0d — doc-status check

`tests/test_doc_evidence.py::test_no_in_progress_status_with_recorded_gate`
fails when a `docs/*.md` file carries both `Status: in progress` and a
recorded gate result (`**Gate result:**` / `**Gate status**:` / a `Gate`
heading). `docs/PHASE9_2.md` had exactly that and is corrected.

### 0e — suite-table evidence guard

`scripts/phase10/check_suites.py` verifies the result tables in
`docs/PHASE7.md`, `docs/PHASE8.md`, `docs/PHASE8_5.md`, `docs/PHASE8_6.md`
and the verdict-counts table in `docs/VALIDATION.md` against the committed
suite JSONs (`runs/suite.json`, `suite8.json`, `xeval_phase7.json`,
`suite8_5.json`, `suite8_6.json`, `float32_rerun.json`). Checked:

- per-spot EVs, ΔEV chips / % of pot, mean/max frequency diffs,
  exploitability percentages, verdicts, tree-check node counts;
- cross-evaluation percentages (TS reported / TS-in-ours / ours) and
  mapped-infoset `N/M` fractions;
- EV-gap table rows and aggregate prose claims (verdict counts,
  "N of the M", `≤` bounds, ranges);
- rounding: a printed doc value must equal the artifact value rounded to
  the doc's displayed precision (half-ulp tolerance).

Negative tests: `tests/test_doc_evidence.py` parametrizes one mutation per
covered doc (edited temp copy → guard must exit 1).

Doc corrections the guard forced (stale printed values vs committed JSON):

- `PHASE7.md`: verdict note said `explained (10)`; `suite.json` holds 11.
- `PHASE8.md`: `w-pair-cap0` max |Δf| printed `0.27`; artifact `0.265`.
- `PHASE8_5.md`: `turn-narrow-b`/`w-turn-cap0` ΔEV %pot printed `0.069`/
  `0.035`; artifacts `0.034`/`0.018`.
- `VALIDATION.md`: Phase-8 row read `6 match / 2 explained`; `suite8.json`
  holds `5 / 3`.

## Skips (0a)

From `pytest -q -rs` in `.venv-engine` (the API suite skips nothing):

| Test | Reason |
|------|--------|
| `tests/test_phase4_concurrency.py` | skipped: could not import 'fastapi' — an API-venv test file, excluded on Python 3.9 |
| `tests/test_phase4_failures.py` | skipped: could not import 'fastapi' — API-venv test file |
| `tests/test_phase4_gpu.py` | skipped: could not import 'fastapi' — API-venv test file |
| `tests/test_phase5.py` | skipped: could not import 'fastapi' — API-venv test file |
| `tests/test_phase8_aggregation.py` (2 skips) | skipped: api package needs the api venv |
| `tests/test_protocol.py` | skipped: `sys.stdlib_module_names` needs Python 3.10+ |

All seven are deliberate per-runtime exclusions (`tests/conftest.py`), none
unexplained. GPU-marked tests run in the API suite on both RTX 5060 Ti
cards.

## SSE runs (0b)

`test_sse_finished_and_cancelled_jobs`: **50 consecutive runs, all
passed** (`pytest -q tests/test_api_sse.py::test_sse_finished_and_
cancelled_jobs`, `.venv-api`).
`tests/test_api_sse.py` (11 tests): **20 consecutive runs, all passed.**

(An earlier attempt ran the two loops concurrently and collided on the
fake worker's fixed socket `/tmp/holdem_solver_1000/gpu0.sock` —
`WorkerStartError`, an artifact of the harness, not the SSE code; all
reported runs above are from clean serial loops.)

## Stream logs (0c)

Committed under `scripts/phase10/logs/`.

## Artifact gaps (0e)

Every artifact the guard checks is committed: `scripts/phase7/runs/
suite.json`, `phase8/runs/{suite8,xeval_phase7,float32,random_batch,
random_spots}.json`, `phase8_5/runs/{suite8_5,float32_rerun}.json`,
`phase8_6/runs/{suite8_6,*_ts}.json`.

One deliberate gap: the per-spot `*_ts.json` TexasSolver dumps for phases
7/8/8.5 are gitignored (PHASE7.md: "Raw dumps and TS inputs are kept in
scripts/phase7/runs/ (gitignored)"). Doc tables cite only the committed
suite JSONs, so the guard remains self-contained; the gap is recorded
here, not fixed.

## Part-0 gate

- `scripts/run_all_tests.sh` on the gpu-5060ti outpost: exit 0 —
  engine 160 passed / 7 skipped (listed above), api 225 passed /
  0 skipped, collection coverage PASS.
- SSE file: 20/20 consecutive runs pass; cancelled-job test 50/50.
- Guard negative tests: 7 parametrized mutations + doc-status check
  all pass in `tests/test_doc_evidence.py` (17 tests).

## Part-0 gate

- `scripts/run_all_tests.sh` green on the outpost (both GPUs present);
- `tests/test_api_sse.py` stable over 20 runs, the cancelled-job test over
  50;
- `check_suites.py` exits 0 and fails on every mutated doc.

---

# Phase 10 — Part 1: web UI at /ui

Date: 2026-11-17. Status: complete.

A React + TypeScript + Vite app in `ui/`, built to `ui/dist/` and mounted
on the API at `/ui` (deep links fall back to `index.html`; `/` redirects
to `/ui/`). Data flows through a client generated from the OpenAPI schema
(`ui/scripts/gen-api.sh` → `openapi-typescript` → `openapi-fetch`) plus
TanStack Query; no request shapes are hand-written.

Auth follows the cookie contract: `POST /v1/auth/login` exchanges the key
for a stateless session cookie + a readable `holdem_csrf` cookie; every
mutation sends its value in `x-csrf-token`; logout clears the client
cookie only. SSE ids (`<boot>-<seq>`) are opaque; `snapshot` replaces
state, `progress` below the current iteration is dropped, `done`/`gone`
close the stream, and the browser's built-in reconnect (Last-Event-ID
replay) is relied on.

New API surface for the UI (Part 1):

- `GET /v1/solves` — newest-first job list (`?status=` filter).
- `POST /v1/estimate` — nodes/VRAM prediction without queueing.
- `SolveStatus` gains `pinned` and `spot_name`.
- `/ui` static mount with SPA fallback (`_SPAFiles`).

## Test cases (fixed)

1. Playwright against the fake engine (`ui/tests/e2e.spec.ts`, server
   `ui/scripts/serve-fake.sh` on an isolated runtime dir): login
   (bad key rejected → good key lands on jobs); build a river spot,
   estimate, solve, view root, navigate to a child via the tree
   navigator; save/rename/tag/duplicate a spot and solve it from the
   library; SSE reconnect after a dropped events stream; inline 422 on
   a 3-card board. Full transcript: `scripts/phase10/logs/
   playwright_e2e.log`.
2. One real end-to-end run on the outpost with both GPUs
   (`ui/scripts/real-e2e.mjs` against the systemd API on :8000): log in,
   build a river spot, estimate, solve twice (one job per GPU), watch
   the live chart to `done`, open the strategy viewer at root and a
   child, save a spot. Screenshots:
   `scripts/phase10/logs/01-login.png` … `07-spot-library.png`;
   job status snapshot: `scripts/phase10/logs/real_e2e.json`.

## Part-1 results

Playwright suite: **6 passed** (15.9 s) — see `playwright_e2e.log`.

Real run (`real_e2e.json`, `04-jobs-done.png`):

| job | gpu | iterations | exploitability | elapsed | nodes |
|-----|-----|-----------:|---------------:|--------:|------:|
| `1da488305561` | 0 | 120 | 0.0931 | 1.22 s | 12,556 |
| `08a0453b5a93` | 1 | 120 | 0.2259 | 1.11 s | 9,361 |

Both finished `done` with `strategy_available` true; the strategy grid,
tree children, and per-hand table rendered (05/06).

## UI limits (documented)

- Range editor weight slider edits the focused class only; drag paints
  the brush value uniformly (no per-drag partial weights).
- Reach lists in hand detail are truncated to the first 20 entries
  (sorted by share).
- Compare screen shows strategy grids only — no EV/frequency table diff;
  histories are typed manually there (no tree click-through).
- Strategy grid cells summarize a node's action mix; individual combo
  inspection needs the `per combo` toggle on hand detail.
- Dead-card picker allows up to 8 cards (river only); deck cap is 5 for
  board.
- No bulk job operations (cancel/pin are per-row), no delete for jobs.
- SSE chart starts empty for jobs that finished before the page opened
  (points come from live progress events only).
- The SPA is served from `ui/dist`; a missing build disables `/ui`
  entirely (API still works).

## Part-1 gate

- Playwright suite green: 6/6 (`playwright_e2e.log`).
- Real run recorded on both GPUs (`real_e2e.json`, screenshots 01–07).
- UI limits listed above.
- `check_suites.py` verifies Part-1 numbers against `real_e2e.json`,
  `playwright_e2e.log`, and the committed screenshots.
