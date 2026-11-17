# Phase 9 — UI backend (no UI code)

Date: 2026-11-17. Status: complete — merged as PR #14; the Phase 9.1 branch fixed the corrupted evidence artifact (see PHASE9_1.md).

Everything in the tolerances section is written **before** implementation and
is not revised after seeing results. No vendored changes (GPU-CFR,
TexasSolver untouched).

## Tolerances and gates (fixed)

Standing conventions carry over from VALIDATION.md: both solvers solved to
≤ **0.5% of pot** exploitability for validation; "standing EV tolerance" is
|ΔEV| ≤ **1.0% of pot** + 0.05 chips.

1. **Tree navigation.** `GET /v1/solves/{id}/tree?history=...` must return
   player, street, pot, effective stacks, board (including dealt river cards
   from the history), canonical actions with labels, a children summary, and
   `is_chance` with dealt-card options for turn street-ends. Every field must
   be derivable without the client parsing the history string. Gate: on a
   real solved spot, the root node's pot equals the submitted pot, the
   board equals the submitted board, and the action set matches the
   strategy endpoint's actions for the same history.
2. **Per-node values.** `GET /v1/solves/{id}/node?history=...` returns
   reach-weighted action frequencies per hand class (and per combo with
   `combos=true`) using the validated aggregation
   `w(c)·own_reach(c)·opp_mass(c)` (PHASE8.md), per-action EV, hand EV at the
   node, equity vs the opponent's reaching range, and both players' reaches.
   EV gates:
   - Root EV (p0) computed by the API-side walk equals the job's
     `profile_value_p0` status value within the standing EV tolerance.
   - On **6 river cap-0 spots** solved to ≤0.5% of pot, per-combo per-action
     EVs equal TexasSolver-side EVs from the Phase 7 `dump_ev` walk
     (`scripts/phase8/xeval.py::node_action_evs`) within the standing EV
     tolerance per shared (node, combo, action).
   - Property tests (pytest): per-combo action frequencies sum to
     1 ± 1e-4; child reach = parent reach × action frequency for the acting
     player (opponent reach unchanged) within 1e-6 relative; equity in
     [0, 1]; hand EV within the subtree's achievable min/max showdown value.
3. **Live progress.** `GET /v1/solves/{id}/events` is server-sent events
   carrying status, iteration, exploitability, exploitability_iteration.
   Reconnect-safe: a `Last-Event-ID` header resumes after the named event
   (buffered replay); a stale/unknown id still receives a current-state
   snapshot first. The existing poll endpoint is unchanged.
4. **Saved spots.** `/v1/spots` CRUD + duplicate + `POST
   /v1/spots/{id}/solve`. Limits: ≤ 512 spots, name ≤ 200 chars, notes ≤
   2000 chars, ≤ 32 tags of ≤ 64 chars (defaults; env-configurable). A
   pinned solve (`POST /v1/solves/{id}/pin`, `.../unpin`) is skipped by
   strategy retention. Spots and pins survive a process restart (tested).
5. **Browser-safe auth.** `POST /v1/auth/login` issues an HttpOnly,
   SameSite session cookie plus a non-HttpOnly CSRF cookie (returned in the
   body too); mutating requests authenticated by cookie require a matching
   `X-CSRF-Token` header (403 otherwise). API-key auth is unchanged for
   scripts. CORS off by default; `HOLDEM_CORS_ORIGINS` enables it. Login
   rate-limited (default 10/min per client, burst 5 → 429).
6. **Subtree cache.** The custom tree builder builds one deal's subtree and
   copies it per deal (the `_Template`/`_stamp_deal` mechanism). Gate: the
   cap-0 consistency gate and the Phase 6/8.6 equal-tree checks still pass
   with identical node counts; spec-build time measured on the 10M-node
   raise spot (`river-full-pot`, raise_cap 2) and compared with the Phase 6
   recorded figure (61 s spec build, 10.37M nodes).
7. **Docs.** OpenAPI examples for every new endpoint, README section,
   ARCHITECTURE.md, VALIDATION.md updated with the EV validation results.

**Phase gate:** `scripts/run_all_tests.sh` green; new endpoints exercised
against real solves on **both GPUs** with curl output under
`scripts/phase9/logs/`; every number reported in docs copied from those logs.

## Formulas (written before implementation)

At a node with history `h` for acting player `p`, per own combo `c`:

- Own reach: `reach_p(c) = w(c)·Π σ_p(c, a_t)` over `p`'s actions in `h`.
- Opponent conditioned mass: `opp_mass(c) = Σ_{d∈p̄} reach_p̄(d)·[d≁c]`
  (compatible with `c` and the board).
- Action frequency at `h` for a hand class `H`:
  `freq(H, a) = Σ_{c∈H} w(c)·own_reach(c)·opp_mass(c)·σ_p(c, a) /
                Σ_{c∈H} w(c)·own_reach(c)·opp_mass(c)`.
- Per-combo action EV: `EV(c, a)` = the acting player's payoff of the child
  subtree walked with `reach_p = {c: 1}` and the opponent's full reach,
  divided by `opp_mass(c)` (conditional EV; same convention as
  `scripts/phase8/xeval.py::node_action_evs`).
- Hand EV at `h`: `EV(c) = Σ_a σ_p(c, a)·EV(c, a)`; per hand class the
  reach-weighted mean.
- Root EV: recursive walk from the root with `reach_p(c) = w(c)`; showdown
  value via `rank7` (5-card rank over 7 cards), chance nodes averaged over
  remaining river cards; sign-flipped for player 1.
- Equity: for own combo `c`, `equity(c) = Σ_d reach_p̄(d)·[d≁c]·win(c,d) /
  opp_mass(c)` averaged over undealt river cards on turn spots, where
  `win ∈ {1, 0.5, 0}` by `rank7` comparison.
- Node reach (both players): `reach_p̄(d)` normalized to sum 1 over the
  compatible combos, reported per hand class (per combo with `combos=true`).

## Step outcomes

Recorded below as each numbered spec step lands (done / skipped+reason /
failed+analysis). Curl evidence: `scripts/phase9/logs/`; validation JSON:
`scripts/phase9/runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`,
`tree_gate.json`. (Phase 9.1 note: the `ev_validation.json` committed at
this phase's close was a stale 4-spot partial run — a shorter invocation
had overwritten the 6-spot artifact. Phase 9.1 renamed per-run outputs and
re-ran the full 6-spot validation; the artifact above is the corrected,
committed full run.)

1. **Tree navigation — done.** `GET /v1/solves/{id}/tree` returns player,
   street, pot, per-player stacks, board (with dealt cards folded into the
   history), canonical actions + labels, `is_chance`, `dealt_cards`, and a
   children summary with each child's kind/player/actions — no client-side
   history parsing. Terminal histories 404 (`{"detail":"terminal node"}`);
   bad histories 422. `logs/tree_root.txt`: root of a real solve —
   pot 20.0, stacks {p0: 100.0, p1: 100.0}, board KsJsTh7d2c, actions
   check/b10/b20 with children. `logs/tree_check.txt`,
   `logs/tree_bet.txt` walk `check` and `check,b10`.
2. **Per-node values — done.** `GET /v1/solves/{id}/node` returns per
   hand class (per combo with `combos=true`): reach-weighted action
   frequencies via the Phase 8 aggregation, per-action EVs, hand EV,
   equity, and both players' reaches (`ranges.p0`/`p1` with `reach` and
   `share` fields). `logs/node_root.txt` shows all four hand classes with
   frequencies, action_evs, ev, equity.
   **EV validation** (`scripts/phase9/validate_node_evs.py`, corrected in
   Phase 9.1): **6/6 cap-0 river spots pass within tolerance** —
   1,756 (node, combo, action) EV values compared between `evwalk.py` and
   the Phase 7/8 dump_ev walk, max |Δ| = 7.993605777301127e-15 chips
   (float64 round-off); per spot: 84 / 273 / 392 / 266 / 229 / 512 for
   w-toy-check, w-dry-cap0, w-wet-cap0, w-pair-cap0, w-blocker-cap0,
   w-full-cap0 (`runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`).
   Root EVs agree to ~1e-14 (full-precision digits in the artifact); the
   strategy source and dtype for each comparison are recorded there.
   Property tests in `tests/test_evwalk.py`:
   frequencies sum to 1, child reach = parent reach × σ, equity ∈ [0,1],
   walk payoff on a fold/showdown.
3. **Live progress — done.** `GET /v1/solves/{id}/events` streams SSE
   `snapshot` / `progress` / `done` events carrying status, iteration,
   exploitability, exploitability_iteration. `logs/events_sse.txt` shows
   the live stream; `logs/events_reconnect.txt` shows `Last-Event-ID: 1`
   replaying from `id: 2` onward. Poll endpoint unchanged.
4. **Saved spots — done.** Full CRUD (`logs/spot_create/get/list/patch/
   delete.txt`), `POST /v1/spots/{id}/duplicate` (`spot_duplicate.txt`),
   `POST /v1/spots/{id}/solve` (queues a real solve, `spot_solve.txt`).
   Limits: 512 spots, name ≤ 200, notes ≤ 2000, ≤ 32 tags × ≤ 64 chars —
   all enforced with 422/429. Pin: `logs/pin.txt`/`unpin.txt` flip
   `pinned`; `expired_strategies` skips pinned rows. Restart persistence
   tested (`test_api_phase9.py::test_spots_survive_restart`).
5. **Browser-safe auth — done.** `POST /v1/auth/login` →
   `holdem_session` (HttpOnly, SameSite=strict, 12 h) +
   `holdem_csrf` cookie (`logs/login.txt` shows both `Set-Cookie`
   headers). Cookie auth reads `GET`s; mutating requests without
   `x-csrf-token` → 403 "missing or mismatched CSRF token"
   (`cookie_no_csrf.txt`), with the token → 201 (`cookie_csrf.txt`).
   Bearer key still works. `logout` clears both cookies. CORS off by
   default — `cors_probe.txt`: `Origin: https://example.com` gets no
   `access-control-*` headers; `HOLDEM_CORS_ORIGINS` enables it. Login is
   rate-limited (10/min, burst 5 → 429; tested).
6. **Subtree cache — verified, mechanism unchanged since Phase 6.**
   `scripts/phase9/tree_gate.py` re-ran `solve_ours` on all 6 Phase 8
   cap-0 spots and all 4 Phase 8.6 spots: `compare_trees` ok on every
   spot and node counts are identical to the recorded suites
   (973 / 5,086 / 10,216 / 3,871 / 3,601 / 29,458 cap-0; 558,253 /
   415,045 / 2,269 / 8,101 phase-8.6). Spec-build on `river-full-pot`
   cap 2: **68.01 s, 10,374,211 nodes** — identical node count, +12% vs
   the Phase 6 recorded 60.7 s (machine variance; no code path changed
   — honest figure, no fabricated "before" number). The `_Template`/
   `_stamp_deal` copy-per-deal mechanism already shipped in Phase 6;
   this phase verified the invariants rather than changing the builder.
7. **Docs — done.** OpenAPI examples for every new endpoint (the
   `test_openapi_example_per_endpoint` gate covers it), README section,
   ARCHITECTURE.md, VALIDATION.md updated.

**Phase gate:** `scripts/run_all_tests.sh` green (engine 143, api 195 —
see `logs/run_all_tests.txt`); endpoints demonstrated on both GPUs —
job `b905de9c0501` ran on gpu 0, `e82d19066e0e` on gpu 1
(`status_a/b.txt`).
