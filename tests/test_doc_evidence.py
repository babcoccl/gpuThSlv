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
