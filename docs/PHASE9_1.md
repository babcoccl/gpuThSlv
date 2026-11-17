# Phase 9.1 — evidence integrity, SSE contract, validation hardening

Date: 2026-11-17. Status: complete (gate green — see Step outcomes).

Everything in this file's tolerances/test-case section is written **before**
implementation and is not revised after seeing results.

## Tolerances and test cases (fixed)

Standing conventions carry over from VALIDATION.md: both solvers solved to
≤ **0.5% of pot** exploitability for validation; "standing EV tolerance" is
|ΔEV| ≤ **1.0% of pot** + 0.05 chips.

1. **Evidence integrity.**
   - `scripts/phase9/validate_node_evs.py` writes each run to a distinct
     artifact whose filename contains the spot list
     (`ev_validation_<spot>+<spot>+....json`). The artifact embeds a manifest:
     spot names, per-spot comparison counts, strategy dtype and source per
     comparison, git commit, timestamp. A run over a proper subset of the 6
     cap-0 river spots never overwrites the full-run artifact.
   - The full 6-spot run is re-executed; the artifact and its stdout log are
     committed. Every number in docs/PHASE9.md, docs/VALIDATION.md and
     README.md about this validation cites that exact artifact file, and
     records the **actual** counts even if they are not 1,657.
   - `scripts/phase9/check_evidence.py` fails (exit 1) if a cited number —
     spot count, per-spot or total (node, combo, action) comparison counts,
     max |Δ| — disagrees with the committed artifact.
   - Gate: `python scripts/phase9/check_evidence.py` exits 0.
2. **EV validation strength.**
   - Root-EV equality is reported at full float precision (repr digits, not
     %.6f), in the artifact and the log.
   - Each comparison in the artifact states the strategy source
     (`texas_dump` average strategy vs our `solve_ours` average strategy)
     and dtype.
   - Independent check: `scripts/phase9/brute_ev.py` shares no code with
     `dump_ev` or `evwalk.py` — own card parsing, own 7-card showdown
     evaluator, own dump-JSON tree walk. For 2 river spots (w-toy-check,
     w-dry-cap0) it computes each acting combo's hand EV at the root and at
     one child node by brute force (per-opponent-combo enumeration, one
     pairwise walk per (own, opp) combo pair) and compares to the
     `evwalk`/`node_action_evs` conditional EVs aggregated by the profile.
     Same standing EV tolerance. Any difference is documented with its size.
3. **SSE contract.** `GET /v1/solves/{id}/events` implements exactly:
   - Every connection (new or reconnecting) **starts with a `snapshot`
     event** carrying the job's current state — never skipped.
   - `Last-Event-ID` `<boot>-<seq>` valid and still in the replay buffer:
     replay buffered events with seq > it **in order**, then go live.
   - Stale, unknown, malformed, future, boot-mismatched (pre-restart), or
     finished job: send the snapshot, then live events (or the terminal
     `done`), i.e. no replay — the snapshot re-syncs the client.
   - Event ids are `<boot>-<seq>`: `boot` is a per-process token so a
     pre-restart id can never alias a new buffer's seq.
   - `retry:` field present; `: keep-alive` heartbeat comments at least
     every ~20 s; response headers `Cache-Control: no-cache` and
     `X-Accel-Buffering: no` (disable proxy buffering).
   - Tests (pytest, fake engine): new client; valid reconnect replays only
     seq > last; stale id; evicted id (buffer overflow); malformed id;
     future id; finished job (snapshot + done); cancelled job; API restart
     (buffer lost — old id gets snapshot, no replay); two concurrent
     clients both receive snapshot; buffer overflow still serves snapshot.
   - README client rule: the snapshot **replaces** local state; `progress`
     events with an iteration lower than the current one are ignored;
     `done` is terminal.
4. **Sessions.** Documented and tested: session cookies are stateless
   HMAC-signed (`v1.<expiry>.<sig>`), so they survive an API restart as long
   as the signing material is unchanged (``HOLDEM_SESSION_SECRET`` or the
   API key it is derived from). The login credential is the API key sent to
   `POST /v1/auth/login` as `{"key": "..."}`. Tested: a cookie issued by
   app instance A authenticates against a fresh app instance B on the same
   data dir / key; mutating requests still require CSRF.
5. **Subtree cache.** Record: the `_Template`/`_stamp_deal` cache did **not**
   reduce spec-build time — 68.0 s vs the Phase-6-recorded 60.7 s on the
   10.4M-node `river-full-pot` cap-2 spot (identical node count; machine
   variance, no code path changed). Add a profile (cProfile +
   section timings in `scripts/phase9/profile_build.py`, committed output
   `scripts/phase9/runs/build_profile.txt`) splitting build time into node
   object creation, dict insertion, and the stamping copy. No optimization
   in this phase.
6. **Guard.** `tests/test_doc_evidence.py` fails if any phase doc cites a
   validation artifact (`scripts/*/runs/*.json`) that does not exist or is
   not committed, or cites a comparison count that does not appear in the
   referenced artifact.

**Phase gate:** `scripts/run_all_tests.sh` green; the full 6-spot artifact
committed; SSE tests passing; `check_evidence.py` exit 0.

## Step outcomes

Recorded below as each numbered spec step lands (done / skipped+reason /
failed+analysis).

1. **Evidence integrity — done.** `validate_node_evs.py` now writes
   `runs/ev_validation_<spot>+<spot>+….json` (spot list in the filename);
   each run carries a `_manifest` block: `spots`, `requested_spots`,
   `complete`, `spot_count`, `is_full_run`, `git_commit`, `generated_at`,
   per-spot `node_evs_compared`/`node_evs_max_abs_diff`, strategy `dtype`
   and `strategy_source` per comparison. Partial runs write a
   `<name>.partial` sibling and only produce the named artifact on
   completion — a subset run can never overwrite the full-run artifact.
   The stale `ev_validation.json` (a 4-spot partial run's output under the
   generic name) was deleted. **Full re-run: 6/6 spots pass; actual counts
   are 84 / 273 / 392 / 266 / 229 / 512 = 1,756 (node, combo, action)
   comparisons — not 1,657 — max |Δ| = 7.993605777301127e-15** (float64
   round-off), committed at
   `runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`
   with log `logs/validate_node_evs.txt`. PHASE9.md / VALIDATION.md /
   README.md / PROGRESS.md now cite exactly that artifact and its numbers
   (the earlier "exactly zero" / 1,657 claims were wrong — they described
   an older artifact and were recorded before the file was overwritten).
   `check_evidence.py` exits 0.
2. **EV validation strength — done.** Root EVs print with repr digits in
   the artifact and log (e.g. w-toy-check root `10.063393305812998` vs
   `10.063393305813008`, |Δ| = 1.07e-14); every comparison records
   `strategy_source` (`ts_dump` average strategy vs `ours` solved average
   strategy) and `dtype` (`float64`). Independent check:
   `scripts/phase9/brute_ev.py` shares no code with `dump_ev`/`evwalk.py`
   (own card parse, own `_score5`/`score7` evaluator, own pairwise tree
   walk); on w-toy-check and w-dry-cap0 it recomputes per-combo hand EVs
   at the root and at the `check` child by enumerating all opponent
   combos, plus the root EV: **max |Δ| vs `node_action_evs`/`root_ev` =
   1.5987211554602254e-14** — float64 accumulation order only, far inside
   tolerance (`runs/brute_ev.json`).
3. **SSE contract — done.** `GET /v1/solves/{id}/events` sends `retry:`
   then a `snapshot` of current job state first on every connection; event
   ids are `<boot>-<seq>` (`app.state.sse_boot`, random per process) so a
   pre-restart `Last-Event-ID` can never alias. Valid in-window ids replay
   buffered events after the id (in order) then go live; stale / evicted /
   malformed / future / other-boot ids and finished jobs get snapshot then
   live or `done` — no replay. `Cache-Control: no-cache` +
   `X-Accel-Buffering: no` headers; `: keep-alive` comments every
   `SSE_HEARTBEAT_SECONDS` (15 s, env `HOLDEM_SSE_HEARTBEAT_SECONDS`);
   buffer `HOLDEM_SSE_BUFFER` (256), poll `HOLDEM_SSE_POLL_SECONDS` (0.5),
   retry `HOLDEM_SSE_RETRY_MS` (2000). `tests/test_api_sse.py` covers all
   11 cases: new client, valid reconnect, stale, evicted, malformed,
   future, finished, cancelled, API restart, two concurrent clients,
   buffer overflow + heartbeat/404. README carries the client rule
   verbatim.
4. **Sessions — done.** Documented in README (browser auth): cookies are
   stateless HMAC `v1.<expiry>.<sig>` tokens signed with
   `HOLDEM_SESSION_SECRET` or `sha256("holdem-session:"+api_key)`; they
   **survive an API restart** when the signing material is unchanged;
   login credential = API key in `POST /v1/auth/login {"key": "..."}`.
   `test_session_survives_restart` proves cookie reuse across a fresh app
   instance and that rotating `session_secret` invalidates it.
   `docs/PHASE9.md` status line corrected ("in progress" → complete).
5. **Subtree cache — recorded.** The `_Template`/`_stamp_deal` cache did
   **not** reduce spec-build time: tree_gate measured **68.01 s** vs the
   Phase-6-recorded **60.7 s** on the 10,374,211-node river-full-pot
   cap-2 spot (identical node count). Profile in
   `scripts/phase9/runs/build_profile.txt` (`profile_build.py`): the
   build is 494,010 deals × 21 template nodes; `_stamp_deal` is ~96% of
   `_assemble` (78.3 s of 80.9 s under cProfile). Inside it:
   `NodeSpec.__init__` ~8.6 s, the `ActionSpec` list comprehension ~8.9 s,
   `_terminal_payoff` ~4.2 s, infoset-key `str.replace`/`InfoSetSpec`
   ~1.7 s, dict `get` ~0.1 s. Micro-split of the same loop body: bare
   `NodeSpec` construction 8.38 s, bare dict insertion 1.08 s, the full
   stamping copy 103.57 s (unprofiled second pass — the gap vs 78.3 s is
   the un-cProfiled re-run plus `infosets`/`chance_reach` writes; both
   figures in the artifact). Dict insertion is ~1% of the build; node
   object creation ~10%; the stamping copy's ActionSpec/payoff work is
   the rest. No optimization made.
6. **Guard — done.** `tests/test_doc_evidence.py`: every `runs/*.json`
   cited by docs/ or README must exist and be git-tracked; every claim
   line (comparison counts, `N cap-0` spot counts, `max |Δ| = x`) must sit
   within 6 lines of an artifact citation; and `check_evidence.py` is run
   as a subprocess. It caught real debt on first run: the Phase 8.5
   `float32_rerun.json` / `suite8_5.json` were gitignored-but-cited
   (force-added and committed), `ev_validation.json` was stale-cited in
   PROGRESS.md (fixed), and the new artifacts were uncommitted.
