"""Phase 9.1: doc-numbers-must-match-artifact check.

Fails (exit 1) when:

* the committed full-run artifact (``ev_validation_<6 spots>.json``) is
  missing, untracked by git, or not a complete 6-spot run;
* docs/PHASE9.md, docs/VALIDATION.md or README.md never cite it;
* a validation claim in those docs — spot count (``N cap-0 spots``,
  ``N/N``), a comparison count (``N EVs`` / ``N values`` /
  ``N (node, combo, action)``), or ``max |Δ| = <x>`` — disagrees with the
  artifact;
* a claim line sits more than a few lines away from the citation (claims
  must name their evidence).

Usage: python3 scripts/phase9/check_evidence.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..", "..")
RUNS = os.path.join(HERE, "runs")
sys.path.insert(0, ROOT)

from scripts.phase8.spots import SPOTS  # noqa: E402

CAP0_RIVER = [s.name for s in SPOTS if s.raise_cap == 0 and len(s.board) == 5]
ARTIFACT_NAME = "ev_validation_%s.json" % "+".join(CAP0_RIVER)
ARTIFACT = os.path.join(RUNS, ARTIFACT_NAME)
DOCS = ["docs/PHASE9.md", "docs/PHASE9_1.md", "docs/VALIDATION.md", "README.md"]

# claim patterns: (regex, which artifact values the captured number may equal)
CLAIMS = [
    # "1,756 (node, combo, action) EV values" / "1,756 node/combo/action EVs"
    (re.compile(r"(\d[\d,]*)\s*\(?(?:node[,\s/]*combo[,\s/]*action)\)?\s*"
                r"(?:EV\s+)?(?:values|EVs|comparisons?)", re.IGNORECASE),
     "comparisons"),
    # "N EV values compared" / "N values compared"
    (re.compile(r"(\d[\d,]*)\s+(?:EV\s+)?values compared", re.IGNORECASE),
     "comparisons"),
    # "max |Δ| = 7.99e-15" (a measured value, '=' only — '≤' is a bound)
    (re.compile(r"max\s*\|Δ\|\s*=\s*([0-9.eE+-]+)"), "max_abs_diff"),
    # "6 cap-0 river spots" / "6/6 cap-0" (not "Phase 7 cap-0 ...")
    (re.compile(r"(\d+)\s*/\s*(\d+)\s+cap-0", re.IGNORECASE), "spot_count_pair"),
    (re.compile(r"(?<!Phase )(\d+)\s+cap-0\s+(?:river\s+)?spots",
                re.IGNORECASE), "spot_count"),
]

CITE_WINDOW = 6  # wrapped paragraphs put the citation a few lines away

_CITED = re.compile(r"ev_validation|brute_ev|validate_node_evs")


def _tracked(relpath):
    rc = subprocess.call(
        ["git", "ls-files", "--error-unmatch", relpath],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return rc == 0


def _fmt(md):
    return {repr(md), "%.6f" % md, "%g" % md, "%.2e" % md}


def main():
    failures = []
    rel_artifact = os.path.relpath(ARTIFACT, ROOT)
    manifest = None
    if not os.path.exists(ARTIFACT):
        failures.append("missing artifact %s" % rel_artifact)
    elif not _tracked(rel_artifact):
        failures.append("artifact %s is not committed" % rel_artifact)
    else:
        manifest = json.load(open(ARTIFACT))["_manifest"]
        if not manifest.get("complete"):
            failures.append("artifact %s is marked complete=false" % rel_artifact)
        if sorted(manifest.get("spots", [])) != sorted(CAP0_RIVER):
            failures.append(
                "artifact covers %r, not the full %r"
                % (manifest.get("spots"), CAP0_RIVER)
            )

    counts = set()
    spot_count = None
    max_diff = set()
    if manifest:
        spot_count = str(manifest["spot_count"])
        counts.add(str(manifest["total_node_evs_compared"]))
        counts.update(str(p["node_evs_compared"])
                      for p in manifest["comparisons"].values())
        max_diff = _fmt(manifest["max_abs_diff"])

    for doc in DOCS:
        path = os.path.join(ROOT, doc)
        if not os.path.exists(path):
            continue
        lines = open(path).read().splitlines()
        if doc in ("docs/VALIDATION.md", "README.md", "docs/PHASE9.md") and not any(
            "ev_validation" in l for l in lines
        ):
            failures.append("%s never cites the ev_validation artifact" % doc)
        cited = {i for i, l in enumerate(lines) if _CITED.search(l)}
        for lineno, line in enumerate(lines, 1):
            for rx, kind in CLAIMS:
                for m in rx.finditer(line):
                    if not any(abs(lineno - 1 - c) <= CITE_WINDOW for c in cited):
                        failures.append(
                            "%s:%d validation claim not near an artifact "
                            "citation: %s" % (doc, lineno, line.strip()[:100])
                        )
                        continue
                    if not manifest:
                        continue
                    got = m.group(1).replace(",", "")
                    if kind == "comparisons" and got not in counts:
                        failures.append(
                            "%s:%d comparison count %s not in artifact "
                            "(total %s; per-spot %s)"
                            % (doc, lineno, m.group(1),
                               manifest["total_node_evs_compared"],
                               [p["node_evs_compared"]
                                for p in manifest["comparisons"].values()])
                        )
                    elif kind == "max_abs_diff" and got not in max_diff:
                        failures.append(
                            "%s:%d max |Δ| %s != artifact %r"
                            % (doc, lineno, got, manifest["max_abs_diff"])
                        )
                    elif kind == "spot_count" and got != spot_count:
                        failures.append(
                            "%s:%d spot count %s != artifact %s"
                            % (doc, lineno, got, spot_count)
                        )
                    elif kind == "spot_count_pair" and (
                        m.group(1) != spot_count or m.group(2) != spot_count
                    ):
                        failures.append(
                            "%s:%d pass count %s/%s != artifact spot count %s"
                            % (doc, lineno, m.group(1), m.group(2), spot_count)
                        )

    for f in failures:
        print("FAIL: %s" % f)
    if failures:
        sys.exit(1)
    print("OK: doc numbers match %s" % rel_artifact)


if __name__ == "__main__":
    main()
