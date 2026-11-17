"""Phase 9.2: doc claims parsed from the documents must match the artifacts.

The hand-maintained claim table is gone: every claim-shaped number in
``docs/*.md`` and ``README.md`` is parsed out of the prose itself and must
(a) sit within a few lines of an artifact citation and (b) equal the value
the cited artifact records. The guard knows two artifacts:

* ``runs/ev_validation_<spots>.json`` — ``_manifest``: spot list + order,
  ``spot_count``, per-spot ``node_evs_compared`` +
  ``node_evs_max_abs_diff``, ``total_node_evs_compared``, ``max_abs_diff``;
* ``runs/brute_ev.json`` — per-node ``compared`` / ``rows`` combo counts,
  per-node ``max_abs_diff`` and ``root_ev.abs_diff``.

Claim shapes checked against them:

* totals — ``N (node, combo, action) EV values``, ``N node/combo/action
  EVs``, ``N values compared``, ``N values``;
* spot counts — ``N cap-0`` (river / spots / anything) and ``N/N cap-0``;
* ``max |Δ| = <x>`` (also ``\\|Δ\\|`` in tables, ``vs ... =`` variants);
* slash-separated per-spot lists — ``84 / 273 / ... / 512``: a list of
  exactly ``spot_count`` numbers near a citation maps to the manifest's
  spot order; each element must equal that spot's ``node_evs_compared``,
  the sum must equal the total, and any spot names given beside the list
  must appear in manifest order;
* bare ``N/M`` fractions near a citation — both integers must appear in
  the artifacts' claim values (spot count, totals, per-spot counts, and
  brute_ev's per-node compared/rows counts).

A claim-shaped match with no citation within CITE_WINDOW fails too (claims
must name their evidence).

Usage: python3 scripts/phase9/check_evidence.py [doc ...]
       (docs default to docs/*.md + README.md; args may be repo-relative or
       absolute paths, which lets tests point the guard at temp copies)
"""
from __future__ import annotations

import glob
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
BRUTE_ARTIFACT = os.path.join(RUNS, "brute_ev.json")
REQUIRED_CITERS = ("README.md", "docs/VALIDATION.md", "docs/PHASE9.md")

CITE_WINDOW = 6  # wrapped paragraphs put the citation a few lines away
_CITED = re.compile(r"runs/[\w+._-]+\.json|ev_validation|brute_ev|validate_node_evs")

_COMPARISONS = re.compile(
    r"(\d[\d,]*)\s*\(?(?:node[,\s/]*combo[,\s/]*action)\)?\s*"
    r"(?:EV\s+)?(?:values|EVs|comparisons?)"
    r"|(\d[\d,]*)\s+(?:EV\s+)?values\s+compared"
    r"|(\d[\d,]*)\s+values\b"
    r"|(\d[\d,]*)\s+EVs\b",
    re.IGNORECASE,
)
_MAX_DIFF = re.compile(
    r"max\s*\\?\|Δ\\?\|\s*(?:vs\s+[^=\n]*?)?=\s*([0-9.eE+-]+)",
    re.IGNORECASE,
)
_SPOT_PAIR = re.compile(r"(?<![\d.])(\d+)\s*/\s*(\d+)\s+cap-0", re.IGNORECASE)
_SPOT_COUNT = re.compile(r"(?<!Phase )(?<![\w,-])(\d+)\s+cap-0\b", re.IGNORECASE)
_SLASH_LIST = re.compile(r"(?<![\d.])(\d[\d,]*(?:\s*/\s*\d[\d,]*)+)(?![\d.])")
_BARE_FRACTION = re.compile(r"(?<!Phase )(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d.]|cap-0)")
_RUN_REF = re.compile(r"(?:scripts/[\w./-]+/)?runs/[\w+._-]+\.json")


def _tracked(relpath):
    rc = subprocess.call(
        ["git", "ls-files", "--error-unmatch", relpath],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return rc == 0


def _fmt(x):
    return {repr(x), "%.6f" % x, "%g" % x, "%.2e" % x}


def _ev_values(manifest):
    """Claim values the ev_validation artifact explains."""
    order = manifest["spots"]
    per_spot = [manifest["comparisons"][s]["node_evs_compared"] for s in order]
    ints = {manifest["spot_count"], manifest["total_node_evs_compared"], *per_spot}
    # an unqualified "max |D|" claim is the full run's maximum; a per-spot
    # maximum is only allowed when the claim names that spot
    diffs = {manifest["max_abs_diff"]}
    spot_diffs = {
        s: manifest["comparisons"][s]["node_evs_max_abs_diff"] for s in order
    }
    return {
        "order": order, "per_spot": per_spot, "ints": ints,
        "diffs": diffs, "spot_diffs": spot_diffs,
    }


def _brute_values(data):
    """Claim values the brute_ev artifact explains."""
    ints, diffs = set(), set()
    for spot in data.values():
        for node in (spot.get("nodes") or {}).values():
            ints.add(node["compared"])
            if node.get("rows") is not None:
                ints.add(node["rows"])
            diffs.add(node["max_abs_diff"])
        root = spot.get("root_ev") or {}
        if root.get("abs_diff") is not None:
            diffs.add(root["abs_diff"])
    return {"ints": ints, "diffs": diffs}


def _cited_windows(cited, i):
    """Artifact kinds cited within CITE_WINDOW of line index i.

    Only the two artifacts this guard can read provide claim values:
    ``brute_ev`` -> "brute"; ``ev_validation`` / ``validate_node_evs`` ->
    "ev". Any other ``runs/*.json`` citation is "other": it satisfies
    the citation requirement but contributes no values, so a claim it
    cannot carry fails the value check.
    """
    arts = set()
    for c in cited:
        if abs(c - i) <= CITE_WINDOW:
            text = cited[c]
            if "brute_ev" in text:
                arts.add("brute")
            elif "ev_validation" in text or "validate_node_evs" in text:
                arts.add("ev")
            else:
                arts.add("other")
    return arts


def _allowed(cited_arts, ev, brute):
    ints, diffs = set(), set()
    if "ev" in cited_arts:
        ints |= ev["ints"]
        diffs |= ev["diffs"]
    if "brute" in cited_arts:
        ints |= brute["ints"]
        diffs |= brute["diffs"]
    return ints, diffs


def main():
    failures = []
    docs = sys.argv[1:] or sorted(
        os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "*.md"))
    ) + ["README.md"]

    rel_artifact = os.path.relpath(ARTIFACT, ROOT)
    manifest = ev = None
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
        ev = _ev_values(manifest)

    brute = {"ints": set(), "diffs": set()}
    if os.path.exists(BRUTE_ARTIFACT):
        brute = _brute_values(json.load(open(BRUTE_ARTIFACT)))

    spot_name_re = None
    if ev:
        spot_name_re = re.compile("|".join(re.escape(s) for s in ev["order"]))

    for doc in docs:
        path = doc if os.path.isabs(doc) else os.path.join(ROOT, doc)
        if not os.path.exists(path):
            continue
        lines = open(path).read().splitlines()
        cited = {i: l for i, l in enumerate(lines) if _CITED.search(l)}
        if doc in REQUIRED_CITERS and not any(
            "ev_validation" in l for l in lines
        ):
            failures.append("%s never cites the ev_validation artifact" % doc)

        seen = set()  # (lineno, kind, value) — joined-line matches repeat

        def flag(lineno, why, line):
            failures.append("%s:%d %s: %s" % (doc, lineno, why, line.strip()[:100]))

        ev_empty = {"ints": set(), "diffs": set(), "spot_diffs": {}}
        for i, line in enumerate(lines):
            # claims may wrap: also scan this line joined with the next
            candidates = [line]
            if i + 1 < len(lines):
                candidates.append(line + " " + lines[i + 1])
            for text in candidates:
                # a match's claim line: matches starting inside lines[i]
                # belong to i; anything past the join boundary to i + 1
                def claim_line(m, split=len(line)):
                    return i if m.start() < split else i + 1

                # -- totals / values -------------------------------------
                for m in _COMPARISONS.finditer(text):
                    mi = claim_line(m)
                    got = next(g for g in m.groups() if g)
                    key = (mi, "comparisons", got)
                    if key in seen:
                        continue
                    seen.add(key)
                    near = _cited_windows(cited, mi)
                    ints, _diffs = _allowed(near, ev or ev_empty, brute)
                    if not near:
                        flag(mi + 1, "comparison claim not near an artifact citation", line)
                    elif int(got.replace(",", "")) not in ints:
                        flag(
                            mi + 1,
                            "comparison count %s not in cited artifact(s)"
                            " (ev total %s, per-spot %s; brute compared %s)"
                            % (got,
                               manifest["total_node_evs_compared"] if manifest else "?",
                               ev["per_spot"] if ev else "?",
                               sorted(brute["ints"])),
                            line,
                        )

                # -- max |Δ| ----------------------------------------------
                for m in _MAX_DIFF.finditer(text):
                    mi = claim_line(m)
                    key = (mi, "maxdiff", m.group(1))
                    if key in seen:
                        continue
                    seen.add(key)
                    near = _cited_windows(cited, mi)
                    _ints, diffs = _allowed(near, ev or ev_empty, brute)
                    # an unqualified max |Δ| is the run's overall maximum;
                    # a per-spot diff counts only when that spot is named
                    # beside the claim (artifact filenames stripped first)
                    if ev and "ev" in near and spot_name_re:
                        named = spot_name_re.findall(
                            _RUN_REF.sub("", " ".join(lines[mi:mi + 3]))
                        )
                        diffs |= {ev["spot_diffs"][s] for s in named}
                    ok = any(
                        m.group(1) in {repr(d), "%.6f" % d, "%g" % d, "%.2e" % d}
                        for d in diffs
                    )
                    if not near:
                        flag(mi + 1, "max |Δ| claim not near an artifact citation", line)
                    elif not ok:
                        flag(
                            mi + 1,
                            "max |Δ| %s not in cited artifact(s) (%s)"
                            % (m.group(1), sorted(diffs)),
                            line,
                        )

                # -- spot counts ------------------------------------------
                for m in _SPOT_PAIR.finditer(text):
                    mi = claim_line(m)
                    key = (mi, "pair", m.group(0))
                    if key in seen:
                        continue
                    seen.add(key)
                    near = _cited_windows(cited, mi)
                    if not near:
                        flag(mi + 1, "spot count not near an artifact citation", line)
                    elif not ev or "ev" not in near or (
                        m.group(1) != str(manifest["spot_count"])
                        or m.group(2) != str(manifest["spot_count"])
                    ):
                        flag(
                            mi + 1,
                            "pass count %s/%s != artifact spot count %s"
                            % (m.group(1), m.group(2),
                               manifest["spot_count"] if manifest else "?"),
                            line,
                        )
                for m in _SPOT_COUNT.finditer(text):
                    mi = claim_line(m)
                    key = (mi, "spotcount", m.group(0))
                    if key in seen:
                        continue
                    seen.add(key)
                    near = _cited_windows(cited, mi)
                    if not near:
                        flag(mi + 1, "spot count not near an artifact citation", line)
                    elif not ev or "ev" not in near or m.group(1) != str(manifest["spot_count"]):
                        flag(
                            mi + 1,
                            "spot count %s != artifact %s"
                            % (m.group(1),
                               manifest["spot_count"] if manifest else "?"),
                            line,
                        )

                # -- slash-separated per-spot lists -------------------------
                for m in _SLASH_LIST.finditer(text):
                    mi = claim_line(m)
                    near = _cited_windows(cited, mi)
                    parts = [p.strip() for p in m.group(1).split("/")]
                    numbers = [int(p.replace(",", "")) for p in parts]
                    # spot names may wrap onto the next line(s); the
                    # artifact filename itself names every spot — strip
                    # runs/*.json references before extracting names
                    names = (
                        spot_name_re.findall(
                            _RUN_REF.sub("", " ".join(lines[mi:mi + 3]))
                        )
                        if spot_name_re
                        else []
                    )
                    is_claim = (
                        ev
                        and len(numbers) == manifest["spot_count"]
                        and (near or len(names) >= 2)
                    )
                    if not is_claim:
                        continue
                    key = (mi, "slashlist", m.group(1))
                    if key in seen:
                        continue
                    seen.add(key)
                    if not near:
                        flag(mi + 1, "per-spot count list not near an artifact citation", line)
                        continue
                    if "ev" not in near:
                        flag(mi + 1, "per-spot count list not beside the ev_validation citation", line)
                        continue
                    if numbers != ev["per_spot"]:
                        flag(
                            mi + 1,
                            "per-spot counts %s != artifact manifest order %s"
                            % (numbers, ev["per_spot"]),
                            line,
                        )
                    if sum(numbers) != manifest["total_node_evs_compared"]:
                        flag(
                            mi + 1,
                            "per-spot counts sum %d != artifact total %d"
                            % (sum(numbers), manifest["total_node_evs_compared"]),
                            line,
                        )
                    if len(names) >= 2 and names != [
                        s for s in ev["order"] if s in names
                    ]:
                        flag(
                            mi + 1,
                            "spot order %s differs from manifest %s"
                            % (names, ev["order"]),
                            line,
                        )

                # -- bare N/M fractions -------------------------------------
                for m in _BARE_FRACTION.finditer(text):
                    mi = claim_line(m)
                    # a fraction inside a longer slash list is the list's
                    # claim, not a standalone N/M
                    if re.search(r"/\s*$", text[:m.start()]) or \
                            re.match(r"\s*/", text[m.end():]):
                        continue
                    key = (mi, "fraction", m.group(0))
                    if key in seen:
                        continue
                    seen.add(key)
                    near = _cited_windows(cited, mi)
                    if not near:
                        continue  # generic fractions need no citation
                    # only compared/rows-style fractions are checkable —
                    # that's brute_ev's claim space
                    if "brute" not in near:
                        continue
                    a, b = int(m.group(1)), int(m.group(2))
                    if a not in brute["ints"] or b not in brute["ints"]:
                        flag(
                            mi + 1,
                            "fraction %s has no counterpart in cited "
                            "artifact(s) values %s" % (m.group(0), sorted(brute["ints"])),
                            line,
                        )

    for f in failures:
        print("FAIL: %s" % f)
    if failures:
        sys.exit(1)
    print("OK: doc numbers match %s" % rel_artifact)


if __name__ == "__main__":
    main()
