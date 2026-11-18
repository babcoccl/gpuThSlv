# Phase 10.5 — last validation-tooling fixes

Date: 2026-11-18. Status: done (gate below).

Everything in the test-case section below was written **before**
implementation and is not revised after seeing results.

## Scope

1. Import boundary, fail closed. Every `scripts/**/*.py` and
   `tests/**/*.py` file declares its runtime venv (`.venv-engine`,
   `.venv-api`, or both) in its docstring; the boundary test fails on
   undeclared files. Engine seeds = every `engine_worker/**` and
   `protocol/**` module plus every file declaring `.venv-engine` plus
   every file importing `gpu_cfr` or `engine_worker`; the walk follows
   local imports transitively (module and function level, `scripts.*`
   and `holdem_solver.*`) and fails if it reaches `holdem_solver.api`.
   New: a declared-venv-vs-imports consistency test (a file whose
   import closure reaches `holdem_solver.api` must not declare
   `.venv-engine`; a file with a module-level `gpu_cfr` import must not
   declare `.venv-api`), a negative test with a temp script declaring
   `.venv-engine` that reaches `holdem_solver.api` through a helper
   (must fail), and a runtime import hook installed in engine-venv
   test runs that raises on any `holdem_solver.api` import. Reconcile
   `scripts/phase9/brute_ev.py`, which still declares `.venv-engine`
   while importing `holdem_solver.api` — its docstring contradicts the
   code; the declaration moves to `.venv-api`.
2. Atomic-export crash tests inject failures inside the real write
   path of `_export_node_evs` — ENOSPC partway through `write()`, at
   `fsync`, and at `os.replace` — using the exporter's real temp-file
   name from one shared helper (`nodes.tmp_path_for`). Assertions per
   case: the temp file exists at failure time, is removed afterwards,
   no final file appears, and a pre-existing valid final file is left
   intact. A mutation test reruns the crash check against an exporter
   copy with the cleanup removed and requires it to fail. The
   `/dev/full` test stays as an additional check.
3. Self-healing: when `load_node_evs` declines a file (missing,
   pre-checksum, corrupt, stale), the first `/node` regenerates the
   node-EV table and atomically persists a new `*.evs.npz`, so the
   file heals itself. Regeneration is single-flight per job — N
   concurrent first requests trigger exactly one compute+write.
4. The evec-vs-evwalk comparison gains a relative tolerance
   `|diff| <= 1e-12 * max(1, |ref|)` alongside the absolute 1e-9: a
   cell passes when either bound holds. `max_rel_diff` is reported per
   check; the river aggregate's 2.0e-10 worst abs diff is
   ≈2.3e-14 relative (root |EV| ≈ 5.7e4) — the doc and the guard now
   carry the relative number.
5. `*.evs.npz` format v2: one chained `crc32` over both payload arrays
   (`crc32(mass.tobytes(), crc32(ev.tobytes()))`) replaces the XOR of
   two crc32s, plus a `format` member; the loader declines files
   without `format == 2` (pre-10.5 files decline cleanly and the
   self-heal path rewrites them on first `/node`).
6. `docs/PROGRESS.md` gains the Phase 10.5 entry.

## Test cases (fixed)

### 1 — import boundary

`tests/test_import_boundary.py` (collected by both venvs, pure `ast`):

- `test_every_script_and_test_declares_venv`: every
  `scripts/**/*.py` and `tests/**/*.py` module docstring names at
  least one of `.venv-engine` / `.venv-api` — failure lists the
  undeclared paths.
- `test_engine_venv_never_imports_api`: seeds = `engine_worker/*.py` +
  `protocol/*.py` + every scripts/tests file declaring `.venv-engine`
  + every scripts/tests file importing `gpu_cfr` or
  `holdem_solver.engine_worker` (any level). Transitive walk over
  `holdem_solver.*`, `scripts.*`, `tests.*` imports at module and
  function level; reaching `holdem_solver.api` fails and names the
  importing file and the edge.
- `test_declared_venv_matches_imports`: per file, if its transitive
  closure reaches `holdem_solver.api` it must not declare
  `.venv-engine`; if it has a module-level `gpu_cfr` /
  `gpu_cfr.*` import it must not declare `.venv-api`.
- `test_declared_venv_matches_imports_negative`: a tmp tree with
  `helper.py` (`from holdem_solver.api import evec`) and `script.py`
  declaring `.venv-engine` that imports `helper` through a second
  hop must be flagged by the same checker function.
- Runtime hook: `tests/conftest.py` installs a `sys.meta_path` finder
  in the engine venv (Python <3.10) raising `ImportError` on
  `holdem_solver.api*` — a regression test asserts the hook is in
  place during engine runs.

### 2 — atomic-export crash tests

In `tests/test_api_evec.py`, driving the real
`Scheduler._export_node_evs` on the fixture strategy:

- `test_export_node_evs_crash_mid_write`: `_open_for_write` returns a
  handle that raises `OSError(ENOSPC)` after N bytes; the real tmp
  path (`api_nodes.tmp_path_for(evs_path)`) exists at failure time,
  is gone afterwards, no final file appears.
- `test_export_node_evs_crash_at_fsync`: `os.fsync` patched to raise;
  same assertions.
- `test_export_node_evs_crash_at_replace`: `os.replace` patched to
  raise; same assertions.
- Each crash case runs twice: with no prior file and with a valid
  prior `evs.npz`, which must survive byte-identical.
- `test_export_node_evs_cleanup_mutation`: the exporter function's
  source is copied with the `tmp.unlink()` cleanup removed, exec'd
  into a stub scheduler, driven into the mid-write crash, and the
  leftover-`.tmp` assertion must fail — proving the test catches the
  mutation.
- `/dev/full` stays as an additional end-to-end check.

### 3 — self-healing /node

- `test_node_evs_regenerates_on_first_node_request`: job with a valid
  strategy but missing/corrupt `evs.npz`; first `/node` returns the
  same values as live compute AND rewrites the file (format v2,
  checksum valid); a second `/node` serves from the regenerated file.
- `test_node_evs_regen_single_flight`: with the compute wrapped in a
  barrier, K concurrent first `/node` requests produce exactly one
  `export_node_evs` call and one file write.
- Pre-10.5 (format-less) and stale-keys files are regenerated too.

### 4 — relative tolerance

`compare_cells` gains `max_rel_diff = max(|got-ref| / max(1, |ref|))`
over compared cells; `evec_all_nodes.py` reports it for
`aggregate_edge_value`, `parity_split` and `exact_per_combo` and
passes when `max_abs_diff <= 1e-9 or max_rel_diff <= 1e-12`. The
PHASE10_3 table keeps its abs columns; the guard asserts the same
either-bound on the evec-vs-evwalk checks (all measured rels land
below 1e-12 anyway). PHASE10_4 §5's
river note records the relative error ≈2.3e-14.

### 5 — format v2

`_write_evs_npz` helper and `load_node_evs` move to `format == 2` and
the chained crc32; every corruption test still returns `None`, plus a
v1 file (old XOR checksum, no `format` member) returns `None`.

## Results

- Import boundary: 5 tests pass in `.venv-engine` (incl. the temp-tree
  negative and the active runtime-hook test); the hook test is skipped
  in `.venv-api`. `brute_ev.py` declared `.venv-api`; every scripts and
  tests file carries a venv declaration.
- Crash tests: 6 crash cases (3 injection points × with/without prior
  file) pass; the mutation test detects the removed cleanup.
- Self-heal: 4 decline kinds regenerate on first `/node`; the
  single-flight test observes exactly 1 `export_node_evs` call across
  8 concurrent requests.
- `evec_all_nodes.json` regenerated with `max_rel_diff`: river agg
  2.29e-14, split 3.09e-14, exact 3.12e-14; all ≤ 1e-12.
- Format v2: v1 evs.npz files decline (`load_node_evs` → None) and the
  on-disk scale-job files were regenerated in v2.

## Gate

`scripts/run_all_tests.sh` on the outpost: PASS —
engine 212 passed/7 skipped, api 301 passed/1 skipped, collection
coverage and ui checks green; `check_suites.py` evidence guard OK
(24 docs). The negative tests assert the boundary violation is
detected (temp-tree declaration test) and the mutation test requires
the leftover-`.tmp` assertion to fail — both verified inside the
suite.
