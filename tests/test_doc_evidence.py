"""Phase 9.1 guard: a doc may not cite a validation count with no matching
committed artifact.

Two checks, both cheap (no solves):

1. Every ``runs/*.json`` file referenced by docs/ or README.md exists and
   is tracked by git — a renamed or uncommitted artifact fails.
2. Every line carrying a validation claim (a comparison count like
   "N EVs"/"N values compared"/"N (node, combo, action)", a spot count
   "N cap-0", or "max |Δ| = <x>") cites such an artifact within 4 lines.

Plus the strict per-number check in
``scripts/phase9/check_evidence.py`` (doc numbers vs the committed
ev_validation manifest) is run as a subprocess.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(ROOT.glob("docs/*.md")) + [ROOT / "README.md"]

RUN_REF = re.compile(r"(?:scripts/[\w./-]+/)?runs/[\w+._-]+\.json")
CLAIM = re.compile(
    r"\d[\d,]*\s+\(?node[,\s/]*combo[,\s/]*action\)?"
    r"|\d[\d,]*\s+(?:EV\s+)?(?:values|EVs|comparisons?)\b"
    r"|\d+\s*/\s*\d+\s+cap-0|(?<!Phase )\d+\s+cap-0\s+(?:river\s+)?spots"
    r"|max\s*\|Δ\|\s*=\s*[0-9.eE+-]+",
    re.IGNORECASE,
)
CITE_WINDOW = 6


def _resolve(ref, doc):
    """Map a runs/*.json citation to a repo path."""
    ref = ref.lstrip("./")
    if ref.startswith("scripts/"):
        return ROOT / ref
    # bare "runs/<name>.json": search scripts/*/runs/
    hits = sorted(ROOT.glob("scripts/*/runs/" + ref.split("runs/", 1)[1]))
    return hits[0] if hits else ROOT / ("scripts/x/runs/" + ref)


def _tracked(path):
    return subprocess.call(
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(ROOT))],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) == 0


def test_every_cited_artifact_exists_and_is_committed():
    missing = []
    for doc in DOCS:
        lines = doc.read_text().splitlines()
        for i, line in enumerate(lines):
            for m in RUN_REF.finditer(line):
                path = _resolve(m.group(0), doc)
                if not path.exists():
                    missing.append("%s:%d cites %s — file missing" % (doc.name, i + 1, m.group(0)))
                elif not _tracked(path):
                    missing.append("%s:%d cites %s — not committed" % (doc.name, i + 1, m.group(0)))
    assert not missing, "\n".join(missing)


def test_validation_claims_cite_an_artifact():
    orphans = []
    for doc in DOCS:
        lines = doc.read_text().splitlines()
        cited = {i for i, l in enumerate(lines) if RUN_REF.search(l)}
        for i, line in enumerate(lines):
            if CLAIM.search(line) and not any(abs(i - c) <= CITE_WINDOW for c in cited):
                orphans.append("%s:%d %s" % (doc.name, i + 1, line.strip()[:110]))
    assert not orphans, (
        "validation counts with no artifact citation nearby:\n" + "\n".join(orphans)
    )


def test_ev_validation_numbers_match_committed_artifact():
    """scripts/phase9/check_evidence.py: spots, comparison counts and max
    |Δ| in PHASE9.md / PHASE9_1.md / VALIDATION.md / README.md must equal
    the committed full-run ev_validation manifest."""
    proc = subprocess.run(
        [sys.executable, "scripts/phase9/check_evidence.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# Phase 9.2: the guard parses claims out of the docs, so each edited copy
# of docs/PHASE9.md must fail for its own reason.
CHECK_EVIDENCE = ROOT / "scripts" / "phase9" / "check_evidence.py"
_SOURCE = (ROOT / "docs" / "PHASE9.md").read_text()

_MUTATIONS = {
    "per_spot_count": _SOURCE.replace(
        "84 / 273 / 392 / 266 / 229 / 512",
        "84 / 273 / 392 / 266 / 229 / 513",
    ),
    "total": _SOURCE.replace("1,756", "1,757"),
    "spot_count": _SOURCE.replace("6/6 cap-0", "5/6 cap-0"),
    "max_abs_diff": _SOURCE.replace(
        "7.993605777301127e-15", "7.993605777301128e-15"
    ),
    "spot_order": _SOURCE.replace(
        "w-wet-cap0, w-pair-cap0", "w-pair-cap0, w-wet-cap0"
    ),
}


def _guard(*doc_paths):
    return subprocess.run(
        [sys.executable, str(CHECK_EVIDENCE), *map(str, doc_paths)],
        cwd=ROOT, capture_output=True, text=True,
    )


@pytest.mark.parametrize("case", sorted(_MUTATIONS))
def test_check_evidence_fails_on_mutated_doc(tmp_path, case):
    mutated = tmp_path / "PHASE9.md"
    mutated.write_text(_MUTATIONS[case])
    assert mutated.read_text() != _SOURCE, "mutation %s did not change the doc" % case
    proc = _guard(mutated)
    assert proc.returncode == 1, (
        "guard passed mutated doc (%s):\n%s" % (case, proc.stdout)
    )


# Phase 10: docs/PHASE7|8|8_5|8_6.md and VALIDATION.md result tables must
# equal the committed suite JSONs (scripts/phase10/check_suites.py).
CHECK_SUITES = ROOT / "scripts" / "phase10" / "check_suites.py"


def _suites_guard(*doc_paths):
    return subprocess.run(
        [sys.executable, str(CHECK_SUITES), *map(str, doc_paths)],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_suite_tables_match_committed_artifacts():
    proc = _suites_guard()
    assert proc.returncode == 0, proc.stdout + proc.stderr


_SUITE_MUTATIONS = {
    # verdict-count note must equal the suite verdict histogram
    "phase7_verdict_count": ("PHASE7.md", "explained (11)", "explained (10)"),
    # a verdict cell flipped in the step-2 table
    "phase8_verdict": (
        "PHASE8.md",
        "| w-blocker-cap0 | 0 | yes | +0.18 | 2.7 | 1.00 | 0.292% | 0.492% | match |",
        "| w-blocker-cap0 | 0 | yes | +0.18 | 2.7 | 1.00 | 0.292% | 0.492% | explained |",
    ),
    # a cross-eval percentage off in the last digit
    "phase8_xeval": (
        "PHASE8.md",
        "| w-turn-cap0 | 0.048% | 16.55% | 0.074% | 5424/5838 |",
        "| w-turn-cap0 | 0.048% | 16.56% | 0.074% | 5424/5838 |",
    ),
    # xeval ts-in-ours off in the turn table
    "phase8_5_xeval": (
        "PHASE8_5.md",
        "| turn-narrow-b | 0.034% | 0.063% | match |",
        "| turn-narrow-b | 0.034% | 0.064% | match |",
    ),
    # float32 delta cell off
    "phase8_5_float32": (
        "PHASE8_5.md",
        "| river-dry-cap0 | 1.62e-07 | 6.24e-06 |",
        "| river-dry-cap0 | 1.62e-07 | 6.25e-06 |",
    ),
    # one last-digit step on a sci-notation cell exceeds half-ulp
    "phase8_5_float32_halfulp": (
        "PHASE8_5.md",
        "| river-dry-cap0 | 1.62e-07 | 6.24e-06 |",
        "| river-dry-cap0 | 1.61e-07 | 6.24e-06 |",
    ),
    # a deleted evidence table is an error, not a pass
    "phase8_5_missing_table": (
        "PHASE8_5.md",
        "| spot | ΔEV (chips) | Δexploitability (pp) |",
        "| spot | ΔEV (chips) | Δexpl pp |",
    ),
    # a malformed mapped-infosets fraction is an error, not a skip
    "phase8_mapping_malformed": (
        "PHASE8.md",
        "| w-turn-cap0 | 0.048% | 16.55% | 0.074% | 5424/5838 |",
        "| w-turn-cap0 | 0.048% | 16.55% | 0.074% | garbage |",
    ),
    # xeval percentage off on the equal-tree table
    "phase8_6_xeval": (
        "PHASE8_6.md",
        "| river-cap3-eq | 3 | equal (19 nodes) | **0.016%** | explained |",
        "| river-cap3-eq | 3 | equal (19 nodes) | **0.017%** | explained |",
    ),
    # verdict count off in the summary table
    "validation_counts": (
        "VALIDATION.md",
        "| Phase 8 weighted (`scripts/phase8/runs/suite8.json`) | 8 | 5 | 3 | 0 |",
        "| Phase 8 weighted (`scripts/phase8/runs/suite8.json`) | 8 | 6 | 3 | 0 |",
    ),
}


@pytest.mark.parametrize("case", sorted(_SUITE_MUTATIONS))
def test_check_suites_fails_on_mutated_doc(tmp_path, case):
    doc_name, old, new = _SUITE_MUTATIONS[case]
    source = (ROOT / "docs" / doc_name).read_text()
    mutated = tmp_path / doc_name
    mutated.write_text(source.replace(old, new))
    assert mutated.read_text() != source, "mutation %s did not change the doc" % case
    proc = _suites_guard(mutated)
    assert proc.returncode == 1, (
        "guard passed mutated doc (%s):\n%s" % (case, proc.stdout)
    )


# Phase 10.2: run_all_tests.sh must wire all four UI contract checks, and
# check-mutation must actually fail when the schema pipeline is broken.
RUN_ALL = ROOT / "scripts" / "run_all_tests.sh"
UI_DIR = ROOT / "ui"


def test_run_all_tests_wires_all_contract_checks():
    text = RUN_ALL.read_text()
    for check in (
        "check-mutation.mjs",
        "check-schema.sh",
        "check-no-fetch.mjs",
        "check-bodies.mjs",
    ):
        assert check in text, "run_all_tests.sh does not run %s" % check


def test_check_mutation_fails_when_schema_regen_is_noop(tmp_path):
    """check-mutation's whole job is detecting that a mutated openapi.json
    breaks tsc. If schema regeneration silently no-ops (schema.d.ts never
    rewritten), tsc passes and the check MUST exit non-zero."""
    import shutil

    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx not on PATH")
    shim = tmp_path / "npx"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "openapi-typescript" ]]; then exit 0; fi\n'
        'exec "%s" "$@"\n' % npx
    )
    shim.chmod(0o755)
    import os

    env = dict(os.environ, PATH="%s:%s" % (tmp_path, os.environ["PATH"]))
    proc = subprocess.run(
        ["node", "scripts/check-mutation.mjs"],
        cwd=UI_DIR, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 1, (
        "check-mutation passed with a no-op schema regeneration:\n"
        + proc.stdout + proc.stderr
    )


def test_every_phase_doc_linked_in_progress():
    """Every docs/PHASE*.md must have a PROGRESS.md entry linking to it."""
    progress = (ROOT / "docs" / "PROGRESS.md").read_text()
    missing = [
        doc.name
        for doc in sorted(ROOT.glob("docs/PHASE*.md"))
        if doc.name not in progress
    ]
    assert not missing, "PHASE docs not linked from PROGRESS.md: %s" % missing


# Phase 10.3: completeness mutations — check_phase10_2 must fail on a
# changed cell, a deleted row or a bogus added row in either table, and
# every other table guard must fail on a row deletion and a row addition.
_P10_2 = "PHASE10_2.md"
_PHASE10_2_MUTATIONS = {
    "main_cell": (
        _P10_2,
        "| scale-river-573k | 573,031 | 5.9 | 8.1 | 0.23 | 200 | 0.400 |",
        "| scale-river-573k | 573,031 | 5.9 | 8.1 | 0.23 | 200 | 0.401 |",
    ),
    "before_cell": (
        _P10_2,
        "| scale-river-573k | 39,246 ms |",
        "| scale-river-573k | 39,247 ms |",
    ),
    "after_cell": (
        _P10_2,
        "| scale-river-573k | 39,246 ms | 64 ms |",
        "| scale-river-573k | 39,246 ms | 65 ms |",
    ),
    "delete_main_row": (
        _P10_2,
        "\n| scale-raisecap2 | 95,743 | 1.1 | 2.7 | 0.63 | 200 | 0.577 | iteration cap | 23 | 3 | 3 | 93 |",
        "",
    ),
    "delete_before_after_row": (
        _P10_2,
        "\n| scale-raisecap2 | 14,916 ms | 23 ms |",
        "",
    ),
    "add_bogus_row": (
        _P10_2,
        "| scale-raisecap2 | 95,743 | 1.1 | 2.7 | 0.63 | 200 | 0.577 | iteration cap | 23 | 3 | 3 | 93 |",
        "| scale-raisecap2 | 95,743 | 1.1 | 2.7 | 0.63 | 200 | 0.577 | iteration cap | 23 | 3 | 3 | 93 |\n"
        "| scale-bogus | 1 | 1 | 1 | 1 | 1 | 1 | none | 1 | 1 | 1 | 1 |",
    ),
}


@pytest.mark.parametrize("case", sorted(_PHASE10_2_MUTATIONS))
def test_check_phase10_2_fails_on_mutated_doc(tmp_path, case):
    doc_name, old, new = _PHASE10_2_MUTATIONS[case]
    source = (ROOT / "docs" / doc_name).read_text()
    mutated = tmp_path / doc_name
    mutated.write_text(source.replace(old, new))
    assert mutated.read_text() != source, "mutation %s did not change the doc" % case
    proc = _suites_guard(mutated)
    assert proc.returncode == 1, (
        "guard passed mutated doc (%s):\n%s" % (case, proc.stdout)
    )


# Row-deletion and row-addition mutations for every other checked table.
_ROW_MUTATIONS = {
    "phase7_results_del": (
        "PHASE7.md",
        "\n| toy-check        | 20 | 10.012 | 10.063 | −0.051 | −0.26 | 1.6  | 6.9  | 0.030 | 0.170 | 1.4 | 0.2 | 29.5 | match |",
        "",
    ),
    "phase7_results_add": (
        "PHASE7.md",
        "| river-full       | 20 | −0.529 | −0.539 | 0.010  | 0.05  | 5.5  | 44.2 | 0.334 | 0.120 | 85.7| 15.8| 30.4 | explained |",
        "| river-full       | 20 | −0.529 | −0.539 | 0.010  | 0.05  | 5.5  | 44.2 | 0.334 | 0.120 | 85.7| 15.8| 30.4 | explained |\n"
        "| bogus-spot       | 20 | 0.000  | 0.000  | 0.000  | 0.00  | 0.0  | 0.0  | 0.000 | 0.000 | 0.0 | 0.0 | 0.0  | match |",
    ),
    "phase8_results_del": (
        "PHASE8.md",
        "\n| w-dry-cap0 | 0 | yes | −0.07 | 2.2 | 0.11 | 0.279% | 0.166% | match |",
        "",
    ),
    "phase8_results_add": (
        "PHASE8.md",
        "| w-full-cap0 | 0 | yes | +0.00 | 4.4 | 0.24 | 0.180% | 0.074% | match |",
        "| w-full-cap0 | 0 | yes | +0.00 | 4.4 | 0.24 | 0.180% | 0.074% | match |\n"
        "| w-bogus | 0 | yes | +0.00 | 0.0 | 0.00 | 0.000% | 0.000% | match |",
    ),
    "phase8_xeval_del": (
        "PHASE8.md",
        "\n| w-toy-check | 0.170% | 0.170% | 0.030% | 42/42 |",
        "",
    ),
    "phase8_xeval_add": (
        "PHASE8.md",
        "| w-full-cap0 | 0.074% | 0.074% | 0.180% | 256/256 |",
        "| w-full-cap0 | 0.074% | 0.074% | 0.180% | 256/256 |\n"
        "| w-bogus | 0.000% | 0.000% | 0.000% | 0/0 |",
    ),
    "phase8_xeval_p7_del": (
        "PHASE8.md",
        "\n| toy-check | 0.170% | 0.170% | 0.030% |",
        "",
    ),
    "phase8_xeval_p7_add": (
        "PHASE8.md",
        "| river-full | 0.120% | 0.120% | 0.334% |",
        "| river-full | 0.120% | 0.120% | 0.334% |\n"
        "| bogus-spot | 0.000% | 0.000% | 0.000% |",
    ),
    "phase8_evgap_del": (
        "PHASE8.md",
        "\n| w-toy-check | 42 | 3.69 | 0.14 |",
        "",
    ),
    "phase8_evgap_add": (
        "PHASE8.md",
        "| w-full-cap0 | 256 | 4.99 | 0.10 |",
        "| w-full-cap0 | 256 | 4.99 | 0.10 |\n| w-bogus | 1 | 0.01 | 0.01 |",
    ),
    "phase8_5_float32_del": (
        "PHASE8_5.md",
        "\n   | toy-check | 2.07e-07 | 3.06e-06 |",
        "",
    ),
    "phase8_5_float32_add": (
        "PHASE8_5.md",
        "   | river-wet-cap0 | 1.64e-06 | 3.14e-06 |",
        "   | river-wet-cap0 | 1.64e-06 | 3.14e-06 |\n   | bogus-spot | 0.0 | 0.0 |",
    ),
    "phase8_5_xeval_del": (
        "PHASE8_5.md",
        "\n   | turn-narrow-b | 0.034% | 0.063% | match |",
        "",
    ),
    "phase8_5_xeval_add": (
        "PHASE8_5.md",
        "   | w-turn-cap0 | 0.018% | 0.069% | **match** — the Phase-8 16.5% xeval gap is closed |",
        "   | w-turn-cap0 | 0.018% | 0.069% | **match** — the Phase-8 16.5% xeval gap is closed |\n"
        "   | bogus-spot | 0.000% | 0.000% | match |",
    ),
    "phase8_6_del": (
        "PHASE8_6.md",
        "\n| river-cap3-eq | 3 | equal (19 nodes) | **0.016%** | explained |",
        "",
    ),
    "phase8_6_add": (
        "PHASE8_6.md",
        "| river-cap3-eq | 3 | equal (19 nodes) | **0.016%** | explained |",
        "| river-cap3-eq | 3 | equal (19 nodes) | **0.016%** | explained |\n"
        "| bogus-cap9 | 9 | equal (1 nodes) | **0.000%** | match |",
    ),
    "validation_verdict_del": (
        "VALIDATION.md",
        "\n| Phase 7 (`scripts/phase7/runs/suite.json`) | 15 | 4 | 11 | 0 |",
        "",
    ),
    "validation_verdict_add": (
        "VALIDATION.md",
        "| Phase 8.6 equal-tree raises (`scripts/phase8_6/runs/suite8_6.json`) | 4 | 1 | 3 | 0 |",
        "| Phase 8.6 equal-tree raises (`scripts/phase8_6/runs/suite8_6.json`) | 4 | 1 | 3 | 0 |\n"
        "| Phase 99 bogus | 1 | 1 | 0 | 0 |",
    ),
    "phase10_jobs_del": (
        "PHASE10.md",
        "\n| `1da488305561` | 0 | 120 | 0.0931 | 1.22 s | 12,556 |",
        "",
    ),
    "phase10_jobs_add": (
        "PHASE10.md",
        "| `08a0453b5a93` | 1 | 120 | 0.2259 | 1.11 s | 9,361 |",
        "| `08a0453b5a93` | 1 | 120 | 0.2259 | 1.11 s | 9,361 |\n"
        "| `bogusjob00000` | 0 | 1 | 0.0000 | 0.00 s | 1 |",
    ),
}


@pytest.mark.parametrize("case", sorted(_ROW_MUTATIONS))
def test_table_guards_fail_on_row_mutations(tmp_path, case):
    doc_name, old, new = _ROW_MUTATIONS[case]
    source = (ROOT / "docs" / doc_name).read_text()
    mutated = tmp_path / doc_name
    mutated.write_text(source.replace(old, new))
    assert mutated.read_text() != source, "mutation %s did not change the doc" % case
    proc = _suites_guard(mutated)
    assert proc.returncode == 1, (
        "guard passed mutated doc (%s):\n%s" % (case, proc.stdout)
    )


# check_evidence.py analogs: dropping one element of the per-spot
# slash-list (row-deletion analog) and adding a bogus max-|Δ| claim
# beside a citation (row-addition analog) must both fail.
_EV_MUTATIONS = {
    "slash_list_row_delete": _SOURCE.replace(
        "84 / 273 / 392 / 266 / 229 / 512",
        "84 / 273 / 392 / 266 / 229",
    ),
    "bogus_claim_add": _SOURCE.replace(
        "w-full-cap0 (`runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`).",
        "w-full-cap0 (`runs/ev_validation_w-toy-check+w-dry-cap0+w-wet-cap0+w-pair-cap0+w-blocker-cap0+w-full-cap0.json`).\n"
        "On bogus-spot, max |Δ| = 1.5e-03.",
    ),
}


@pytest.mark.parametrize("case", sorted(_EV_MUTATIONS))
def test_check_evidence_fails_on_row_mutations(tmp_path, case):
    mutated = tmp_path / "PHASE9.md"
    mutated.write_text(_EV_MUTATIONS[case])
    assert mutated.read_text() != _SOURCE, "mutation %s did not change the doc" % case
    proc = _guard(mutated)
    assert proc.returncode == 1, (
        "guard passed mutated doc (%s):\n%s" % (case, proc.stdout)
    )


def test_no_in_progress_status_with_recorded_gate(tmp_path):
    """A doc may not carry 'Status: in progress' next to a recorded gate
    result — the recorded outcome means the phase is done."""
    bad = tmp_path / "PHASE_TEST.md"
    bad.write_text(
        "# Test\n\nDate: 2026-11-17. Status: in progress.\n\n"
        "**Gate result:** api suite 203 passed / 9 skipped.\n"
    )
    proc = _suites_guard(bad)
    assert proc.returncode == 1, proc.stdout

    good = tmp_path / "PHASE_TEST2.md"
    good.write_text(
        "# Test\n\nDate: 2026-11-17. Status: in progress.\n\n"
        "**Phase gate:** `scripts/run_all_tests.sh` green.\n"
    )
    proc = _suites_guard(good)
    assert proc.returncode == 0, proc.stdout
