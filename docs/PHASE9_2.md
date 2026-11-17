# Phase 9.2 — harden the evidence guard

Date: 2026-11-17. Status: in progress.

Everything in this file's test-case section is written **before**
implementation and is not revised after seeing results.

## Test cases (fixed)

1. **Guard parses claims out of the docs.** `check_evidence.py` drops the
   hand-maintained claim table. Claims are parsed from `docs/*.md` and
   `README.md`: total comparison counts (`N (node, combo, action) EV
   values`, `N node/combo/action EVs`, `N values`, `N values compared`),
   spot counts (`N cap-0` anything, `N/N cap-0`), `max |Δ| = <x>` claims,
   and slash-separated per-spot count lists.
   - A slash list is a per-spot claim when it has exactly `spot_count`
     elements and sits within 6 lines of an artifact citation; its values
     must equal the manifest's per-spot `node_evs_compared` **in manifest
     spot order** and its sum must equal `total_node_evs_compared`.
   - When the claim line or the next line names the spots, the named order
     must be the manifest's order.
   - Any claim-shaped match that disagrees with the cited artifact fails;
     any claim-shaped match more than 6 lines from a citation fails.
   - `max |Δ|` claims check against the cited artifact's recorded diffs —
     `ev_validation_*.json`'s manifest or `brute_ev.json`'s per-node /
     root diffs.
   - Bare `N/M` fractions cited next to the brute-EV artifact must have
     both integers present in its claim values (per-node `compared` and
     `rows` counts).
   - `check_evidence.py` accepts doc paths as positional args so tests can
     point it at temporary copies.
2. **Positive test.** `check_evidence.py` exits 0 on the current docs
   (`tests/test_doc_evidence.py` runs it as a subprocess — existing test
   kept).
3. **Negative tests** (each edits a temp copy of a doc, asserts exit 1):
   - bump one per-spot count inside the slash list;
   - bump the claimed total;
   - drop the `N/N cap-0` pass count to `(N-1)/N`;
   - perturb a recorded `max |Δ|` digit;
   - swap two spot names after the slash list (order must match the
     manifest).
4. **1,657 → 1,756 explanation.** Document the cause with commit ids; if a
   filter changed, name the added nodes.
5. **brute_ev uncompared combos.** Explain the w-dry-cap0 `18/21` figure
   next to `runs/brute_ev.json`: either compare the 3 missing combos or
   document precisely why they cannot be compared. Never write "all
   combos compared" unless true. `brute_ev.py` records `rows` and
   per-combo `skipped` reasons in the artifact so the claim is
   self-verifying on the next run.
6. **Sessions.** README states sessions are stateless and logout does not
   revoke a stolen cookie before expiry; session lifetime is configurable
   via `HOLDEM_SESSION_TTL_SECONDS` (old `HOLDEM_SESSION_SECONDS` still
   honoured as fallback). Tested: env var drives the cookie `max_age` and
   the signed expiry.

**Phase gate:** `scripts/run_all_tests.sh` green; the guard fails on every
negative test; this doc records the step-3 cause.

**Gate result:** engine suite 144 passed / 14 skipped; api suite
203 passed / 9 skipped with **3 failures, none from this phase** —
`test_river_spot_exploitability_decreases` asserts
`measured_vram.reserved_bytes > 0` and needs a real GPU (none on this
host); `test_sse_finished_and_cancelled_jobs` fails identically on `main`
(a cancel-vs-connect race in the app, pre-existing);
`test_sse_evicted_id_gets_snapshot_no_replay` is timing-flaky and passed
on a standalone rerun.

## Step outcomes

Recorded below as each numbered spec step lands.

1. **Guard rewrites claim detection — done.** `check_evidence.py` now
   parses claims straight out of `docs/*.md` + `README.md` (the docs are
   positional args, so tests can point it at temp copies): totals
   (`N (node, combo, action)`, `N values`, `N EVs`, `N values compared`),
   spot counts (`N cap-0`, `N/N cap-0`), `max |Δ| = <x>` (incl. the
   escaped `\|Δ\|` table form and `vs … =` variants), slash-separated
   per-spot lists, and bare `N/M` fractions cited beside
   `runs/brute_ev.json`. Slash lists must equal the manifest's per-spot
   counts **in manifest spot order**, must sum to the manifest total, and
   any spot names beside them must appear in manifest order. Claims more
   than 6 lines from a citation fail. Wrapped claims are matched across
   joined line pairs.
2. **Negative + positive tests — done.** `tests/test_doc_evidence.py`
   parametrizes five mutations of a temp `PHASE9.md` copy — one per-spot
   count, the total, the spot count, a `max |Δ|` digit, and the spot
   order — each must exit 1; the unmutated positive run is the existing
   subprocess test. All 8 pass.
3. **1,657 → 1,756 — explained: no code or data change; a transcription
   error.** Every recorded run of `validate_node_evs.py` on the same six
   spots produced 84 / 273 / 392 / 266 / 229 / 512 = **1,756**
   (node, combo, action) comparisons — the Phase-9 session's own run log
   (2026-11-17 16:49 UTC) and the committed
   `scripts/phase9/logs/validate_node_evs.txt` agree with the manifest in
   `runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`.
   The "1,657" figure was written into PHASE9.md at commit `c3bcfb8`
   (Phase 9) — the same commit's artifact (`ev_validation.json`, the
   generic-named file deleted in Phase 9.1) was a stale 4-spot partial
   summing to 1,399, so even
   the original evidence was internally inconsistent; the number was
   propagated to VALIDATION.md and README.md. The only EV-path commit in
   the window between the doc error and the Phase-9.1 re-measurement is
   `1302fc8` ("advance prefix past `river:` elements; accept
   scientific-notation bets", `evwalk.py`): inert on these spots — the
   river dumps carry no `river:<card>` elements (their chance nodes are
   `deal_number == 0` terminals; `set_dump_rounds` 1) and no
   scientific-notation bet names, so `reach_at`/`replay_chips` behave
   identically and **no nodes were added**. `35df7c9` (Phase 9.1)
   re-measured and recorded 1,756.
4. **brute_ev uncompared combos — explained: there are none.** The
   `18/21` figure on w-dry-cap0 (next to `runs/brute_ev.json`) is
   compared-at-root / compared-at-check, not 18 of 21. TexasSolver emits
   strategy rows only for combos that can be dealt — verified on all
   four committed dumps (`scripts/phase8_6/runs/*_ts.json`: e.g.
   river-cap3-eq keeps 9 of 12 AA/KK rows, dropping the three
   `Ks`-containing KK). At w-dry-cap0's root, range0
   `AKo:0.5,QQ:0.75,22` = 24 combos loses 6 to board-blocking on
   Ks7h2d5c9s (the three `Ks`-holding AKo + the three `2d`-holding 22) →
   18 dealable, **all 18 compared**; the check node's range1
   `AKo:0.25,JJ:0.5,TT` loses 3 (the `Ks`-holding AKo) → 21 dealable,
   **all 21 compared**. A blocked combo's reach is identically zero and
   its conditional hand EV is undefined — it cannot be compared — but no
   emitted row goes uncompared. `brute_ev.py` now records per-node `rows`
   and `skipped` (combo → reason) in the artifact so the next run states
   this explicitly. PHASE9_1.md's "All combos are compared … 18/21" was
   rewritten to the unambiguous "every strategy row the dumps emit is
   compared … 18 + 21".
5. **Sessions — done.** `HOLDEM_SESSION_TTL_SECONDS` configures the
   session lifetime (`HOLDEM_SESSION_SECONDS` kept as fallback);
   `test_session_ttl_seconds_env` verifies it drives both the cookie
   `Max-Age` and the signed expiry. README's browser-auth section now
   states sessions are stateless and logout does not revoke a stolen
   cookie before expiry.

