# Phase 8.6 — Raise-cap equivalence and full turn validation

Resolve the Phase-7/8 raise-count contradiction from TexasSolver source,
re-run turn-narrow-a on a structurally equal tree, and cover raises at the
REST boundary.

## Step 1 — TexasSolver `raise_limit` semantics (from v0.2.0 source)

`bupticybee/TexasSolver` @ `v0.2.0` (95f039c), `src/GameTree.cpp::buildAction`:

- `raise_times` is a single counter carried down the action-line recursion
  of **one street** (a `buildChance` resets it: a new street starts a fresh
  `buildAction` with `raise_times = 0`).
- Generating a **bet** child sets `raise_times = 1` — *the opening bet
  counts toward the limit*. Generating a **raise** child increments it,
  regardless of which player raised — it is shared across players, not
  per player.
- Raises are offered only while `raise_times < raise_limit`, with
  `raise_limit = 4` hard-coded (`include/tools/CommandLineTool.h`,
  `include/runtime/qsolverjob.h`; there is no console command to change it).

So a TexasSolver street allows at most **bet + 3 raises**, the counter is
**shared across players within the street**, and the bet counts as one.

Correction: Phase 8's "D2-extension" line described the limit as "counts
raises per player". Per the source, it counts raise *actions* shared across
players — a cap-1 spot diverges the same way (TS allows 3 raise actions, we
allow 1), so the explanation's conclusion stands; only the counting
mechanism was misstated.

### Equal-tree table (our `raise_cap` vs TS `raise_limit` = 4)

`raise_cap` counts raise actions per street; the bet does not consume it.
Equal trees hold iff TS's third-raise allowance can never produce a node
we lack:

| our `raise_cap` | TS max raises/street | structurally equal? |
|---|---|---|
| 0 | 3 | yes — we emit `raise,` with no sizes (verified Phase 7) |
| 1 | 3 | only if a 2nd raise is impossible (first raise all-in, or stack) |
| 2 | 3 | only if a 3rd raise is impossible (stack exhausted earlier) |
| 3 | 3 | yes |
| ≥ 4 | 3 | only if a 4th raise is impossible (else ours is deeper) |

## Step 2 — Equal-tree reruns (`scripts/phase8_6/`, `runs/suite8_6.json`)

| spot | raise_cap | tree check | xeval (TS in ours) | verdict |
|---|---|---|---|---|
| turn-narrow-a-cap3 | 3 | equal (103 nodes, 0 mismatches) | **0.041%** | match |
| turn-narrow-a-allin | 1 | equal (102 nodes) | **0.347%** | explained |
| river-cap2-eq | 2 | equal (7 nodes) | **0.000%** | explained |
| river-cap3-eq | 3 | equal (19 nodes) | **0.016%** | explained |

Every spot is structurally equal to TS's tree over root+3 levels and every
cross-evaluation lands under the 1.0%-of-pot bar.

- `turn-narrow-a-cap3` is `turn-narrow-a` at cap 3 (donk emitted): the
  D2-ext re-raise line `b5 → r17 → r30` now exists on both sides and the
  15.37% cross-eval collapses to **0.041%** — the Phase-7/8.5 explanation is
  confirmed, and turn validation is full.
- `turn-narrow-a-allin` re-checks the same mechanism at cap 1: making the
  raise all-in (`("x",6.0)`, emitted `raise,125`) removes TS's re-raise
  physically; xeval **0.347%** confirms.
- `river-cap2-eq` verifies cap 2 can be equal: `b10 → r30 → r70` reaches
  all-in on the second raise, so TS's third raise cannot occur.
- `river-cap3-eq` exercises TS's full raise depth (`b10 → r34 → r86.8 →
  r100`) — equal at cap 3.
- "explained" verdicts are per-node mixing frequency diffs (known
  equal-EV-action divergence); all EV gaps ≤ 0.003% of pot.

## Step 3 — REST boundary test

`tests/test_api_gpu.py::test_raises_and_per_player_bet_sizes_over_rest`
posts `raise_cap=1`, `raise_sizes=[["p",0.5]]` and per-player
`bet_sizes={"0":[0.33],"1":[0.66]}` to `/v1/solves`, then checks the built
tree directly against `SpotConfig` semantics: `num_nodes` == 1 + 108 deals
× 15 nodes/deal, and the `/strategy` endpoint's (player, history) → action
set equals the hand-computed tree — including the cap-1 exhaust nodes
offering only fold/call. Real engine, GPU-marked file; 20 iterations.
