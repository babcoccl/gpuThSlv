# Phase 6 — raises

Date: 2026-11-16

## Step 1 — GameSpec spike

Goal: build HUNL trees by hand (raises, per-player/per-street bet sizes)
without touching the vendored `gpu_cfr` package. Everything below was verified
against `third_party/GPU-CFR` in `.venv-engine` (compiled Cython, no source);
the API was learned via `help()` plus probe specs.

### `gpu_cfr.efg.spec`

```python
GameSpec(name: str, root: int,
         nodes: Mapping[int, NodeSpec],
         infosets: Mapping[str, InfoSetSpec],
         chance_reach: Mapping[int, float],
         metadata: Mapping[str, object] | None = None)
NodeSpec(node_id: int, node_type: NodeType, player: int = -1,
         infoset_key: str | None = None, actions: tuple[ActionSpec, ...] = (),
         payoff_p0: float = 0.0)
ActionSpec(name: str, child: int)
InfoSetSpec(key: str, player: int, actions: tuple[str, ...])
NodeType: PLAYER = 0, TERMINAL = 1, CHANCE = 2
```

- `nodes` is a flat dict keyed by integer id; `root` names the root. The
  shipped builders number nodes in DFS order (a node gets its id at creation,
  children are expanded recursively in action order), but the compiler only
  needs a consistent graph — `topological_order` in `efg.graph` walks the
  edges itself.
- `PLAYER` nodes need `player` in `{0, 1}` and an `infoset_key` into
  `infosets`. `CHANCE` and `TERMINAL` nodes leave `player = -1` and
  `infoset_key = None`.
- Terminal payoffs are **player 0's net chips** since the start of the spot:
  showdown → `±(pot_total)/2` where `pot_total = pot + c0 + c1` (lower
  `hunl_showdown_rank` wins), fold by p0 → `-(pot/2) - c0`, fold by p1 →
  `+(pot/2) + c1`. An uncalled bet is not counted (the bettor's own
  contribution only ever enters through the opponent's match).
- `chance_reach` maps the root (1.0) and **every child of every chance node**
  to its probability: deal subtree roots get `1/num_deals`, river-card
  subtree roots get `1/44`. Verified on both shipped builders.
- `metadata` is free-form; `spot_builder` copies `bet_amounts`,
  `turn_bet_amounts`, `river_bet_amounts`, `num_rivers` into `BuildInfo`
  when present.

### `gpu_cfr.efg.compiler`

`compile_game(spec, device_config=None) -> CompiledEFG` flattens the spec to
tensors (`node_type`, `node_player`, `node_infoset`, CSR action tables,
`infoset_*` tables, topo orders, `chance_reach`). `StagewiseCFRSolver(spec,
config)` compiles internally, so a hand-written `GameSpec` is all a custom
builder has to produce. `efg.validation.validate_game_spec(spec)` checks
structural invariants first; a hand-built spec passed it and solved
identically to `build_hunl_river_game` (node-for-node identical dicts,
same exploitability/value — verified on a 16-node toy in the spike).

### `gpu_cfr.efg.efg_file`

`write_efg_file(spec, path)` exports a text format tagged
`gpu_cfr_gamespec_v1`: a header comment block (`Opt { exporter, name,
players }`), then one line per node —
`node n<i> chance actions n<j>=<prob> …`,
`node n<i> player <1|2> actions n<j> …`,
`node n<i> leaf payoffs 1=<p0> 2=<p1>`.
Useful for debugging/export, not needed by the solver path.

### Shipped river grammar (`build_hunl_river_game`)

Verified on a one-deal probe (pot 20, stack 100, fractions 0.5/1.0):

- Root `CHANCE` node, one `ActionSpec` per deal named `"<hand0>:<hand1>"`
  (this naming is required: `set_root_ranges` parses it). Deal order is
  `hands0` outer × `hands1` inner, conflicting pairs skipped; reach
  `1/num_deals`.
- Betting: p0 acts first with `check` plus one `b<amt>` per bet size;
  p1 facing a check can `check` (showdown) or bet; a player facing a bet
  gets exactly `fold` / `call`. No raises, ever.
- Bet amounts are `fraction × pot`, capped at the remaining stack and
  de-duplicated (`stack=10`, fractions `0.5,1.0` → one `b10`). Names are
  `b%g` (`b10`, `b7.5`).
- Infoset key: `p<player>|<hand>|<comma-joined action history>`, history
  made of action names (`check,b10`, …). Each dealt hand gets its own
  infoset per public history.

### Shipped turn grammar (`build_hunl_turn_game`)

- Same street grammar on the turn; every continuing turn line ends in a
  `CHANCE` node dealing each unseen river card (52 − 4 board − 4 hole
  cards = 44), children named by card string, reach `1/44` each.
- River bet fractions apply to the grown pot; metadata
  `river_bet_amounts` maps each turn contribution to its river amounts
  (`((0.0,(10,20)), (10.0,(20,40)), (20.0,(30,60)))`).
- Infoset key on the river:
  `p<player>|<hand>|<turn history>|<river card>|<river history>`.

### Custom builder design (Step 1 build)

`holdem_solver/engine_worker/tree_builder.py` — pure Python, produces a
`GameSpec` directly. Parameters:

- `bet_sizes`: pot fractions, per player, optionally per street
  (`{player: [..]}` or `{street: {player: [..]}}`; a flat list reproduces
  today's shared set).
- `raise_cap`: max raises per street (counted across both players);
  `0` reproduces the shipped grammar exactly.
- `raise_sizes`: sizes for a raise, each either a multiple of the bet
  faced (`("x", m)` → raise to `m × bet`) or a pot fraction
  (`("p", f)` → raise by `f × pot_after_call`, i.e. to `bet + f·(pot+bet)`;
  `f=1` is a pot-sized raise). Capped at the raiser's remaining stack
  (all-in raise), de-duplicated against the call.
- All-in: a bet or raise capped at remaining stack; a player who cannot
  cover a call calls for their remaining stack (showdown with unequal
  contributions); no raises offered when a call already commits the stack
  or `raises_used == raise_cap`.
- Same deal/blocker logic: identical `hands0`/`hands1` enumeration order,
  `"<h0>:<h1>"` action names, `1/num_deals` reach, conflict-free pairs only.
- Action names: `check`, `fold`, `call`, `b%g`, `r%g` (raises only appear
  with `raise_cap > 0`, so a cap-0 spec is byte-identical to the shipped
  grammar in structure, names, and payoffs).

### Consistency gate (Step 1 exit)

With `raise_cap=0` and identical bet sizes, the custom river builder must
produce a `GameSpec` equal to `build_hunl_river_game`'s on node count,
infoset count, **and** solved value/exploitability on three spots (the two
known-answer river spots and one wider spot). Node-for-node equality of the
`nodes` dicts is checked too.

## Step 2 — scaling measurements

Measured on GPU 0 (RTX 5060 Ti 16 GB), float32, `cuda_graph=False`,
`("p",1.0)` raise sizes, 200 CFR+ iterations after a fresh worker per
(spot, cap). Raw rows: `scripts/phase6/logs/raise_measurements.json`
(`scripts/phase6/measure_raises.py`).

| spot | deals | cap | nodes | infosets | spec build s | solver build s | it/s | peak reserved MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| river-toy | 4 | 0 | 37 | 10 | 0.003 | 0.37 | 1193 | 2.0 |
| river-toy | 4 | 1 | 37 | 10 | 0.003 | 0.01 | 1867 | 2.0 |
| river-toy | 4 | 2 | 37 | 10 | 0.003 | 0.01 | 1869 | 2.0 |
| river-narrow | 756 | 0 | 11,341 | 180 | 0.10 | 0.05 | 1809 | 4.0 |
| river-narrow | 756 | 1 | 20,413 | 300 | 0.13 | 0.06 | 1607 | 6.0 |
| river-narrow | 756 | 2 | 29,485 | 420 | 0.16 | 0.08 | 1442 | 8.0 |
| river-broad-standard | 38,202 | 0 | 573,031 | 1,254 | 3.64 | 1.73 | 1775 | 170 |
| river-broad-standard | 38,202 | 1 | 1,031,455 | 2,090 | 6.40 | 3.37 | 1436 | 272 |
| river-broad-standard | 38,202 | 2 | 1,489,879 | 2,926 | 11.94 | 5.24 | 903 | 388 |
| river-broad-wide-allin | 38,202 | 0 | 1,031,455 | 2,090 | 6.29 | 2.99 | 1422 | 290 |
| river-broad-wide-allin | 38,202 | 1 | 1,719,091 | 3,344 | 9.58 | 5.23 | 730 | 448 |
| river-broad-wide-allin | 38,202 | 2 | 2,406,727 | 4,598 | 16.50 | 8.39 | 470 | 628 |
| river-full-pot | 494,010 | 0 | 4,446,091 | 3,160 | 38.8 | 14.5 | 194 | 1162 |
| river-full-pot | 494,010 | 1 | 7,410,151 | 4,740 | 60.0 | 25.3 | 111 | 1882 |
| river-full-pot | 494,010 | 2 | 10,374,211 | 6,320 | 60.7 | 37.0 | 77 | 2642 |

Spots: toy = KAT polarized (`AhQh,4c3c*` vs `9d9h`, pot bet);
narrow = `77-TT` vs `22-66,A2s-A5s`, standard; broad = the Phase 5
`l-broad-standard` range pair vs itself (standard, then wide+all_in);
full-pot = `*` vs `22+,A2+,K2+,Q2+`, half-pot.

Findings:

- **Bytes per node**: reserved VRAM converges to ~266–311 B/node on large
  trees (~266–274 above ~1 MiB; the toy is allocator-floor dominated). The
  `memory.py` estimate (322 B/node reserved) stays conservative at +18%
  margin — no coefficient change needed.
- **Biggest measured tree**: river-full-pot cap 2 = 10.37M nodes / 2.64 GiB.
  At ~267 B/node the 14 GiB card admits ≈ 53M nodes of custom raise tree.
- **Build time dominates**: the pure-Python spec build runs ~6.4 s/M nodes
  (60.7 s for the 10.4M-node tree) vs ~3.6 s/M for solver allocation —
  the engine's shipped builders build at ~10 s/M from compiled code, so
  Python emits nodes ~60% faster than expected but still costs more than
  the solver build itself. For large raise trees the spec build is the
  wall. Proposal: cache per-deal subtrees (every deal hangs an identical
  tree — build once, deepcopy per deal) or vectorize the node emission;
  either cuts spec build to roughly the single-tree time (~1 s) plus a
  copy.
- **it/s**: the raise trees solve slower per iteration (903–1442 vs
  1774–1809 at cap 0) — deeper trees, more edges per iteration — but the
  compiler handles 10.4M-node trees fine (no crash, no truncation).

## Step 3 — known-answer raise test

`tests/test_phase6_raises.py::test_river_check_raise_known_answer` builds
a polarized check-raise toy and checks the solver against an independent
fictitious-play computation of the normal form.

Spot: board `KsQsJd7h2c`, pot 4, stack 1.5. Player 0 = `JJ, 87o` checks;
player 1 = `ATs, KQo, 65o` may bet 1/8 pot; player 0 may fold, call, or
raise to 2× the bet (`r1` = raise to 1.0); player 1 then folds or calls.
Strength order: AT (broadway straight) > JJ (trips) > KQo (two pair) >
87o (pair of sevens) > 65o (air).

Fictitious play over all 264 compatible deals (bound < 0.002): value
+0.327 for player 0; JJ raises 100% for value; 87o calls 82% /
bluff-raises 3%; KQo calls raises 86%; ATs calls 100%; 65o folds raises.
The solver at 60k iterations reproduces it: value 0.3269, exploitability
2e-4, JJ raise = 1.000, 87o call 0.822 / raise 0.030, KQo call-raise
0.859, 65o bluff 9.3%.

Note: equilibria are not unique at combo granularity — an earlier variant
with KK as the value raiser converged to a different valid profile (same
value, raise mass on the KK combos ≈ 0) because KK blocks 4 of KQo's 6
combos, making the marginal KK-vs-KQo raise break-even. JJ shares no
cards with KQo/ATs, so its raise is strictly positive EV in every deal
and the solver's answer is pinned.

## Step 4 — turn builder + API exposure

- `build_turn_spec` gained the same `raise_cap`/`raise_sizes`/
  `river_bet_sizes` parameters; the cap-0 gate passes on two spots
  (`test_cap0_turn_matches_shipped`): identical node/infoset counts to
  `build_hunl_turn_game` and equal solved value/exploitability.
- `SpotConfig` accepts `raise_cap`, `raise_sizes` (`("x", m)` / `("p", f)`)
  and mapping-form `bet_sizes` — `{"0": [...], "1": [...]}` per player or
  `{"turn": [...], "river": [...]}` per street (turn only) — serialized
  through `to_dict`/`from_dict`. `needs_custom_builder` routes such spots
  to `tree_builder`; plain spots keep the shipped builders.
- Gates: `estimate_spot_nodes` counts custom trees exactly (probe holds —
  every deal hangs an identical subtree), so `max_nodes` still applies;
  the API's VRAM admission gate (`predicted > HOLDEM_API_MAX_VRAM_BYTES`)
  covers raise trees since measured reserved bytes/node (~266–311) stay
  under the estimator's 322 B/node. Learned-factor handling is unchanged —
  peaks recorded by jobs feed the same ratio model.
- `test_spot_config_raise_api` covers the wire round-trip, per-street
  turn sizes, the node gate firing, and validation errors (bad raise
  kinds, sizes without a cap, `flop` keys, mixed player/street maps,
  non-positive sizes).
