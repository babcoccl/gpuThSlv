# Changelog

## Phase 8.5 — 2026-11-17

- `POST /v1/solves` now accepts `bet_sizes` as a per-player/per-street dict
  and the `raise_cap`/`raise_sizes` fields (raises were unreachable over
  REST; `SolveRequest` parity fix).
- Build response and `ping` now report the requested `dtype`.
- TexasSolver mapping layer: `emit_donk` emits
  `set_bet_sizes oop,<street>,donk,<sizes>`; `x_raise_pcts` expresses
  `("x",m)` raises as TS pot percents.
- `scripts/run_all_tests.sh` — both-venv test gate with collection
  coverage check; `scripts/check_links.py` — markdown link checker.
- Docs: VALIDATION.md, ARCHITECTURE.md, INDEX.md added; README game-rules
  and API-usage sections rewritten; PHASE7/PHASE8 corrections.

## Phase 8 — 2026-11-17

- `hand_grid` aggregation is reach-weighted (`w·own_reach·opp_mass`).
- Weighted-range suite (8 spots): 6 match / 2 explained vs TexasSolver.
- Cross-evaluation: TS strategy scored in our evaluator reproduces TS's
  exploitability on all 13 identical-tree spots.

## Phase 7 — 2026-11-17

- TexasSolver v0.2.0 comparison harness (`scripts/phase7/`); 15-spot suite:
  4 match / 11 explained / 0 unresolved; root EV within 1% of pot on all.
- Semantic differences decoded and documented (D1–D10).

## Phase 6 — 2026-11-16

- Custom raise-capable tree builder (`raise_cap`, `raise_sizes`,
  per-player/per-street `bet_sizes`, all-in, dead cards); check-raise
  known-answer test matches fictitious play exactly.

## Phases 0–5 — 2026-11-16

- Environment/GO-A, SolverEngine wrapper, process split (JSON-RPC workers),
  REST API, scheduling/operations hardening, dtype + precision work.
  Details in `docs/PHASE*.md` and `docs/PROGRESS.md`.
