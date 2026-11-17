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
