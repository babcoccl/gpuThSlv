# Phase 10.1 — UI API contract hardening + evidence gaps

Date: 2026-11-17. Status: done — gate green on the outpost.

Everything in the test-case section below was written **before**
implementation and is not revised after seeing results.

## Scope

- Route every UI API call through the generated `openapi-fetch` client;
  `client.use` middleware attaches `x-csrf-token` on non-GET requests and
  maps error responses to `ApiError`. The handwritten `request()` helper
  and its raw `fetch` are deleted. `EventSource` (SSE) is the only
  non-generated transport, isolated in `ui/src/api/sse.ts`.
- Remove `Record<string, unknown>` request types and `as never` / `as T`
  casts. Where the generated schema was too loose to type against, the
  server's pydantic models are tightened (typed `bet_sizes` unions,
  `raise_sizes` as `[kind, value]` pairs, `exploitability_history` as a
  typed point, `ranges` as `{p0, p1}`, explicit response models for
  logout and spot delete) and `schema.d.ts` regenerated — never
  hand-written around.

## Test cases (fixed)

### 1 — generated-client coverage

- `grep -rn 'fetch(' ui/src` matches nothing outside
  `src/api/client.ts` / `src/api/sse.ts` (client.ts never calls `fetch`
  directly; `openapi-fetch` does internally).
- `grep -rn 'Record<string, unknown>' ui/src` is empty.
- `grep -rn 'as never' ui/src` is empty; the only `as` casts left are
  narrowing on locally-defined UI types, never to satisfy the API
  contract.
- CSRF: a Playwright request assertion — every mutating call
  (POST/PATCH/DELETE) carries `x-csrf-token`; GETs do not.
- Error mapping: a login with a bad key surfaces `ApiError(401)`;
  a 422 on a bad spot surfaces `ApiError(422)` with `detail` parsed.

### 2 — typed schema

`tsc -b` clean. `SolveRequest.bet_sizes` generates a union
(preset literal | number[] | per-player map | per-street map),
`raise_sizes` generates `[kind, value]` pairs, `exploitability_history`
generates a typed point list, `NodeValuesResponse.ranges` is
`{p0, p1}`, logout and spot-delete have `StatusResponse` bodies.

### 3 — contract checks

- **3a (tsc on mutation).** `ui/scripts/check-mutation.mjs` renames a
  `SolveRequest` request-body field in a temp copy of `openapi.json`,
  regenerates `schema.d.ts`, runs `tsc`, and asserts the compile fails
  (it must fail — the UI field read no longer exists). Both files are
  restored afterwards even on error.
- **3b (schema regen).** `ui/scripts/check-schema.sh` regenerates
  `openapi.json` + `schema.d.ts` into a temp dir and fails on any diff
  vs. the committed files. Wired into `scripts/run_all_tests.sh`.
- **3c (no raw fetch).** `eslint.config.mjs` sets
  `no-restricted-globals: fetch` plus `no-restricted-syntax` rules for
  `fetch(...)`/`new EventSource(...)`/`XMLHttpRequest` over
  `ui/src/**` and `ui/tests/**`; `src/api/client.ts` is exempt from the
  fetch bans, `src/api/sse.ts` from the EventSource ban.
  `ui/scripts/check-no-fetch.mjs` lints clean and asserts the same
  config fails on a `fetch(...)`/`new EventSource(...)` snippet.
- **3d (bodies validate).** `ui/scripts/check-bodies.mjs` builds every
  mutating-call body from fixtures via the same builders the UI uses
  (`src/api/payloads.ts`) and validates each against the committed
  `openapi.json` requestBody schema with ajv (draft 2020-12); any
  violation exits non-zero.

### 4 — chart seeded from history

On a done job opened cold, the exploitability chart renders the
committed `exploitability_history` points before any SSE traffic
(asserted in the Playwright e2e: chart non-empty immediately at `done`).
SSE progress points append deduplicating by `iteration`
(`exploitability_iteration`); a replayed `snapshot` replaces points at
its own iteration, not before.

### 5 — accessibility + keyboard

- `ui/tests/a11y.spec.ts` runs `@axe-core/playwright` on every screen
  (login, jobs, spots, spot setup, a done solve's strategy viewer,
  compare) — **zero serious/critical violations**.
- Keyboard-only tests live in `ui/tests/a11y.spec.ts`: they drive spot
  setup (Tab into the card picker, arrows + Space select the board,
  range text fields accept a pasted string), the jobs list (Tab to a
  job link, Enter opens it), the strategy viewer (Tab into children,
  Enter navigates, breadcrumbs back) and the spot library (Tab to
  Solve/Rename/Duplicate/Delete) using only the keyboard.

### 6 — scale run

On the outpost (systemd API on :8000): a ~573k-node weighted river, a
turn spot (chance nodes at the river deal), and a `raise_cap: 2` river,
each opened in the viewer. Per spot the doc records `/tree` and `/node`
latency + response bytes, time-to-first-render of the viewer, and any
UI errors. Unusable defects are fixed; the rest land in "limits".

### 7 — asserted real e2e

`npm run e2e:real` runs `ui/scripts/real-e2e.mjs` which asserts every
step (login redirect, estimate panel, job done, tree node visible,
child navigation, library save) and exits non-zero on any failure.

### 8 — ΔEV percent-of-pot denominator

Definition verified against the committed suite scripts and stated
below; every ΔEV %pot column in PHASE7.md … PHASE8_6.md is checked
against `ev_diff / spot.pot * 100` in `scripts/phase*/run_suite.py` via
`check_suites.py`.

## Results

### Contract exceptions

None: every UI request body type-checks against the generated schema;
no `as` cast or `Record<string, unknown>` remains in `ui/src`. The one
deliberate non-generated transport is SSE (`src/api/sse.ts`), which the
OpenAPI spec cannot express — it is isolated there and exempted only
for `EventSource`.

### Scale run (item 6)

Outpost systemd API on :8000, two RTX 5060 Ti workers, f32.
Committed artifact: `scripts/phase10/logs/scale-10-1.json`
(produced by `ui/scripts/scale-e2e.mjs`).

| spot | nodes | solve (s) | viewer TTFR (ms) | UI errors |
| --- | ---: | ---: | ---: | ---: |
| river, broad weighted ranges | 573,031 | 49.7 | 299 | 0 |
| turn w/ chance nodes (narrow ranges) | 442,891 | 65.2 | 1,610 | 0 |
| river, `raise_cap: 2` + raise sizes | 95,743 | 39.4 | 2,042 | 0 |

Per-history `/tree` and `/node` measurements (latency ms / response
bytes):

| spot | history | /tree | /node |
| --- | --- | ---: | ---: |
| river-573k | root | 9 ms / 802 B | 39,246 ms / 21.0 KiB |
| river-573k | check | 7 ms / 737 B | 682 ms / 22.2 KiB |
| river-573k | check/b10 | 6 ms / 487 B | 184 ms / 20.9 KiB |
| turn-chance | root | 387 ms / 796 B | 32,309 ms / 2.1 KiB |
| turn-chance | check | 418 ms / 736 B | 18,826 ms / 2.3 KiB |
| turn-chance | check/check (chance) | 353 ms / 5.8 KiB | 422 — chance node |
| raisecap2 | root | 192 ms / 918 B | 14,916 ms / 4.3 KiB |
| raisecap2 | check | 239 ms / 853 B | 8,473 ms / 4.7 KiB |
| raisecap2 | check/b10 | 137 ms / 989 B | 3,362 ms / 5.6 KiB |

Limits recorded, screens usable:

- **Cold `/node` first hit ≈ 8–39 s.** `load_index` rebuilds the
  strategy index from the exported `.npz` on the first request; later
  calls on the same job are ~0.2–9 s depending on subtree size. The
  viewer shows a loading state and stays interactive. A follow-up could
  cache the index per job.
- **`/node` 422 on chance histories is by design** (`/tree` carries the
  dealt-card children); the viewer's navigation already treats chance
  children via `/tree`.
- The turn spot with wide ranges exceeded `max_nodes` (3.9M > 2M);
  the run used `QQ+, AKs` both players — chance-node coverage is what
  the test requires, not node count.

### Asserted real e2e (item 7)

`npm run e2e:real` → `e2e:real PASS` (7 asserted steps: login,
build+estimate, 2-GPU solve, jobs mid-run/done, viewer root+child,
library save). Screenshots in `scripts/phase10/logs/`.

### ΔEV percent-of-pot (item 8)

Definition: **`ev_diff_pct = ev_diff / spot.pot * 100.0`**, where
`spot.pot` is the pot at the root of the spot in chips and `ev_diff`
is absolute EV difference in chips. The identical line appears in
`scripts/phase7/run_suite.py`, `scripts/phase8/run_suite.py`,
`scripts/phase8_5/run_suite.py`, and `scripts/phase8_6/run_suite.py`
(verified by grep), so every ΔEV %pot column in PHASE7.md … PHASE8_6.md
uses it. The PHASE8_5 guard fix (0.069→0.034, 0.035→0.018) matches
this definition: the corrected values divide `ev_diff` by the root pot
(20.0), e.g. 0.68/20.0 = 0.034 and 0.36/20.0 = 0.018.

## Gate

`scripts/run_all_tests.sh` green on the outpost; Playwright suites
(fake + real) green; lint and contract checks pass — and fail correctly
on mutations; numbers in this doc match committed artifacts.
