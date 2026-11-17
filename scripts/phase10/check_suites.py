#!/usr/bin/env python3
"""Phase 10 evidence guard: docs result tables vs committed suite JSONs.

Verifies the result tables and aggregate claims in docs/PHASE7.md,
docs/PHASE8.md, docs/PHASE8_5.md, docs/PHASE8_6.md and the verdict-counts
table in docs/VALIDATION.md against the committed artifacts:

- scripts/phase7/runs/suite.json
- scripts/phase8/runs/{suite8.json,xeval_phase7.json}
- scripts/phase8_5/runs/{suite8_5.json,float32_rerun.json}
- scripts/phase8_6/runs/suite8_6.json

Comparison rule: a printed doc number must equal the artifact value rounded
to the doc's displayed precision (half-ulp tolerance). Percent signs and
the U+2212 minus are normalized. Verdict cells may carry trailing
commentary ("match", "**match** — ..."); the first word decides.

Usage: python3 scripts/phase10/check_suites.py [doc ...]
Exits 1 and prints every disagreement found.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
RUNS = ROOT / "scripts"

errors: list[str] = []


def fail(doc: Path, msg: str) -> None:
    errors.append(f"{doc.name}: {msg}")


def load(rel: str):
    return json.loads((RUNS / rel).read_text())


def clean_num(s: str) -> str:
    return s.replace("−", "-").replace("%", "").replace("**", "").strip()


def decimals(s: str) -> int:
    s = clean_num(s)
    m = re.match(r"-?\d+\.(\d+)e(-?\d+)$", s.lower())
    if m:
        # sci notation: precision = mantissa decimals - exponent
        return len(m.group(1)) - int(m.group(2))
    return len(s.split(".", 1)[1]) if "." in s else 0


def num_close(doc: Path, cell: str, ref: float, what: str) -> None:
    """Doc-printed cell must equal ``ref`` at the cell's own precision."""
    cell = clean_num(cell)
    try:
        v = float(cell)
    except ValueError:
        fail(doc, f"{what}: unparseable number {cell!r}")
        return
    tol = 0.5 * 10 ** (-decimals(cell))
    # float slack proportional to the tolerance: a fixed 1e-9 would exceed
    # half the displayed unit for sci-notation cells like 2.07e-07.
    if abs(v - ref) > tol + max(1e-12, tol * 1e-6):
        fail(doc, f"{what}: doc {cell} vs artifact {ref!r}")


def bound_ok(doc: Path, printed: str, ref: float, what: str) -> None:
    """A printed ``<= B`` bound must hold at the bound's own precision."""
    b = float(printed)
    if round(ref, decimals(printed)) > b:
        fail(doc, f"{what}: bound <= {printed} but artifact value {ref!r}")


def find_table(
    text: str, *markers: str, exclude: tuple[str, ...] = ()
) -> list[tuple[int, list[str]]]:
    """Rows of the first table whose header contains all ``markers`` and
    none of ``exclude``.

    Returns ``[(lineno, cells)]`` for non-separator rows."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if (
            line.strip().startswith("|")
            and all(m in line for m in markers)
            and not any(x in line for x in exclude)
        ):
            rows = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append((j + 1, cells))
                j += 1
            return rows
    return []


def req_table(
    doc: Path, text: str, *markers: str, exclude: tuple[str, ...] = ()
) -> list[tuple[int, list[str]]]:
    """find_table that fails when the table is absent — a missing evidence
    table is an error, not a pass."""
    rows = find_table(text, *markers, exclude=exclude)
    if not rows:
        fail(doc, f"table not found: {', '.join(markers)}")
    return rows


def verdict_of(cell: str) -> str:
    return cell.replace("*", "").strip().split(" ", 1)[0].rstrip("—")


def check_phase7(doc: Path, suite: dict) -> None:
    text = doc.read_text()
    rows = req_table(doc, text, "our EV", "verdict")
    if not rows:
        return
    seen = set()
    for ln, c in rows:
        spot = c[0]
        if spot not in suite:
            fail(doc, f"line {ln}: spot {spot} not in suite.json")
            continue
        s = suite[spot]
        seen.add(spot)
        num_close(doc, c[1], s["pot"], f"{spot} pot")
        num_close(doc, c[2], s["ours"]["ev_p0"], f"{spot} our EV")
        num_close(doc, c[3], s["ts"]["ev_p0"], f"{spot} TS EV")
        num_close(doc, c[4], s["ev_diff_chips"], f"{spot} dEV chips")
        num_close(doc, c[5], s["ev_diff_pct"], f"{spot} dEV %pot")
        num_close(doc, c[6], s["freq"]["mean_abs_diff"] * 100, f"{spot} mean|df| pp")
        num_close(doc, c[7], s["freq"]["max_abs_diff"] * 100, f"{spot} max|df| pp")
        num_close(doc, c[8], s["ours"]["exploitability_pct"], f"{spot} our expl %")
        num_close(doc, c[9], s["ts"]["exploitability_pct"], f"{spot} TS expl %")
        if verdict_of(c[-1]) != s["verdict"]:
            fail(doc, f"{spot} verdict: doc {c[-1]!r} vs {s['verdict']}")
    missing = set(suite) - seen
    if missing:
        fail(doc, f"spots missing from results table: {sorted(missing)}")

    # verdict counts in the notes ("**match (4)**" style)
    for label in ("match", "explained", "unresolved"):
        for m in re.finditer(rf"\b{label} \((\d+)\)", text):
            want = sum(1 for s in suite.values() if s["verdict"] == label)
            if int(m.group(1)) != want:
                fail(doc, f"verdict count {label} ({m.group(1)}) vs artifact {want}")

    # aggregate |dEV| bound
    for m in re.finditer(r"\|ΔEV\| ≤ (\d+\.\d+)%", text):
        mx = max(abs(s["ev_diff_pct"]) for s in suite.values())
        bound_ok(doc, m.group(1), mx, "all-spots |dEV| bound")


def check_phase8(doc: Path, suite: dict, phase7_xeval: dict, suite7: dict) -> None:
    text = doc.read_text()
    phase7_ts = {k: v["ts"] for k, v in suite7.items()}

    rows = req_table(doc, text, "tree ok", "verdict")
    if not rows:
        return
    for ln, c in rows:
        spot = c[0]
        s = suite.get(spot)
        if s is None:
            fail(doc, f"line {ln}: spot {spot} not in suite8.json")
            continue
        ok = c[2].replace("*", "").strip().startswith("yes")
        if ok != s["tree"]["ok"]:
            fail(doc, f"{spot} tree ok: doc {c[2]!r} vs {s['tree']['ok']}")
        num_close(doc, c[3], s["ev_diff_pct"], f"{spot} dEV %pot")
        num_close(doc, c[4], s["freq"]["mean_abs_diff"] * 100, f"{spot} mean|df| pp")
        num_close(doc, c[5], s["freq"]["max_abs_diff"], f"{spot} max|df|")
        num_close(doc, c[6], s["ours"]["exploitability_pct"], f"{spot} our expl %")
        num_close(doc, c[7], s["ts"]["exploitability_pct"], f"{spot} TS expl %")
        if verdict_of(c[8]) != s["verdict"]:
            fail(doc, f"{spot} verdict: doc {c[8]!r} vs {s['verdict']}")

    # cross-eval tables (weighted suite + phase-7 re-run)
    for src, table_rows in (
        (suite, req_table(doc, text, "TS reported", "mapped infosets")),
        (
            phase7_xeval,
            req_table(
                doc, text, "TS reported", "-in-ours",
                exclude=("mapped infosets",),
            ),
        ),
    ):
        for ln, c in table_rows:
            spot = c[0]
            s = src.get(spot)
            if s is None:
                fail(doc, f"line {ln}: xeval spot {spot} not in artifact")
                continue
            xe = s["xeval"] if "xeval" in s else s
            ts_rep = (
                s["ts"]["exploitability_pct"]
                if "ts" in s
                else suite7.get(spot, {}).get("ts", {}).get("exploitability_pct")
            )
            if ts_rep is not None:
                num_close(doc, c[1], ts_rep, f"{spot} xeval TS reported")
            num_close(
                doc, c[2], xe["ts_in_ours"]["exploitability_pct"],
                f"{spot} xeval ts-in-ours",
            )
            num_close(
                doc, c[3], xe["ours"]["exploitability_pct"], f"{spot} xeval ours"
            )
            if len(c) > 4:
                m = re.fullmatch(r"(\d+)/(\d+)", c[4])
                mp = xe["ts_in_ours"]["mapping"]
                if (
                    m is None
                    or int(m.group(1)) != mp["mapped"]
                    or int(m.group(2)) != mp["infosets"]
                ):
                    fail(
                        doc,
                        f"{spot} mapped infosets: doc {c[4]!r} vs "
                        f"{mp['mapped']}/{mp['infosets']}",
                    )

    # EV-gap table
    for ln, c in req_table(doc, text, "EV gap %pot"):
        spot = c[0]
        s = suite.get(spot)
        if s is None or "ev_gap" not in s:
            fail(doc, f"line {ln}: no ev_gap for {spot}")
            continue
        g = s["ev_gap"]
        num_close(doc, c[1], g["combos"], f"{spot} combos measured")
        num_close(
            doc, c[2], g["weighted_freq_diff_pp"], f"{spot} weighted mean|df| pp"
        )
        num_close(doc, c[3], g["ev_gap_pct_pot"], f"{spot} EV gap %pot")

    # "N of the M" identical-tree cross-eval count: pass = our evaluator
    # reproduces TS's reported exploitability (within 0.1pp).
    for m in re.finditer(r"(\d+) of the (\d+)", text):
        n, total = int(m.group(1)), int(m.group(2))
        if total != len(suite) + len(phase7_xeval):
            fail(
                doc,
                f"cross-eval total {total} vs artifacts "
                f"{len(suite) + len(phase7_xeval)}",
            )
        npass = sum(
            abs(
                s["xeval"]["ts_in_ours"]["exploitability_pct"]
                - s["ts"]["exploitability_pct"]
            )
            <= 0.1
            for s in suite.values()
        ) + sum(
            abs(
                s["ts_in_ours"]["exploitability_pct"]
                - phase7_ts[k]["exploitability_pct"]
            )
            <= 0.1
            for k, s in phase7_xeval.items()
            if k in phase7_ts
        )
        if n != npass:
            fail(doc, f"cross-eval count {n} of {total} vs artifacts {npass}")

    # "0.04–0.15% of pot" range over the ev-gap table spots
    for m in re.finditer(r"(\d+\.\d+)–(\d+\.\d+)% of pot", text):
        gaps = [
            s["ev_gap"]["ev_gap_pct_pot"]
            for s in suite.values()
            if "ev_gap" in s and "skipped" not in s["ev_gap"]
        ]
        lo, hi = min(gaps), max(gaps)
        if abs(float(m.group(1)) - lo) > 0.005 or abs(float(m.group(2)) - hi) > 0.005:
            fail(doc, f"EV-gap range {m.group(0)} vs artifacts {lo:.3f}–{hi:.3f}")


def check_phase8_5(doc: Path, suite: dict, float32: dict) -> None:
    text = doc.read_text()

    for ln, c in req_table(doc, text, "Δexploitability"):
        spot = c[0]
        s = float32["spots"].get(spot)
        if s is None:
            fail(doc, f"line {ln}: float32 spot {spot} not in artifact")
            continue
        dev = abs(float(s["float64"]["ev_p0"]) - float(s["float32"]["ev_p0"]))
        dexpl = abs(
            float(s["float64"]["exploitability_pct"])
            - float(s["float32"]["exploitability_pct"])
        )
        num_close(doc, c[1], dev, f"{spot} f32 dEV chips")
        num_close(doc, c[2], dexpl, f"{spot} f32 dexpl pp")

    for ln, c in req_table(doc, text, "xeval ts-in-ours"):
        spot = c[0]
        s = suite.get(spot)
        if s is None:
            fail(doc, f"line {ln}: spot {spot} not in suite8_5.json")
            continue
        num_close(doc, c[1], abs(s["ev_diff_pct"]), f"{spot} |dEV| %pot")
        num_close(
            doc, c[2], s["xeval"]["ts_in_ours"]["exploitability_pct"],
            f"{spot} xeval ts-in-ours",
        )
        if verdict_of(c[3]) != s["verdict"]:
            fail(doc, f"{spot} verdict: doc {c[3]!r} vs {s['verdict']}")

    # prose claims in step 7
    m = re.search(r"freq mean (\d+\.\d+)\s*pp", text)
    if m:
        num_close(
            doc, m.group(1),
            suite["pp-river-3sizes"]["ev_gap"]["weighted_freq_diff_pp"],
            "pp-river-3sizes freq mean pp",
        )
    m = re.search(r"EV gap (\d+\.\d+)%", text)
    if m:
        num_close(
            doc, m.group(1),
            suite["pp-river-3sizes"]["ev_gap"]["ev_gap_pct_pot"],
            "pp-river-3sizes EV gap %pot",
        )
    m = re.search(r"xeval is (\d+\.\d+)%", text)
    if m:
        num_close(
            doc, m.group(1),
            suite["xraise-mixed"]["xeval"]["ts_in_ours"]["exploitability_pct"],
            "xraise-mixed xeval",
        )
    m = re.search(r"All \|ΔEV\| ≤ (\d+\.\d+)%", text)
    if m:
        bound_ok(
            doc, m.group(1),
            max(abs(s["ev_diff_pct"]) for s in suite.values()),
            "all-spots |dEV| bound",
        )
    m = re.search(r"all xeval ≤ (\d+\.\d+)%", text)
    if m:
        step7 = ["pp-river", "pp-river-3sizes", "pp-river-allin",
                 "ps-turn-diff", "pp-turn"]
        bound_ok(
            doc, m.group(1),
            max(suite[k]["xeval"]["ts_in_ours"]["exploitability_pct"]
                for k in step7),
            "step-7 xeval bound",
        )


def check_phase8_6(doc: Path, suite: dict) -> None:
    text = doc.read_text()
    rows = req_table(doc, text, "tree check", "xeval")
    if not rows:
        return
    for ln, c in rows:
        spot = c[0]
        s = suite.get(spot)
        if s is None:
            fail(doc, f"line {ln}: spot {spot} not in suite8_6.json")
            continue
        m = re.match(r"(\w+) \((\d+) nodes(?:, (\d+) mismatches)?\)", c[2])
        if not m:
            fail(doc, f"{spot} tree check cell unparseable: {c[2]!r}")
        else:
            if (m.group(1) == "equal") != s["tree"]["ok"]:
                fail(doc, f"{spot} tree ok: doc {m.group(1)} vs {s['tree']['ok']}")
            if int(m.group(2)) != s["tree"]["checked"]:
                fail(
                    doc,
                    f"{spot} nodes checked: doc {m.group(2)} vs "
                    f"{s['tree']['checked']}",
                )
        num_close(
            doc, c[3], s["xeval"]["ts_in_ours"]["exploitability_pct"],
            f"{spot} xeval",
        )
        if verdict_of(c[4]) != s["verdict"]:
            fail(doc, f"{spot} verdict: doc {c[4]!r} vs {s['verdict']}")

    for m in re.finditer(r"EV gaps ≤ (\d+\.\d+)%", text):
        gaps = [
            s["ev_gap"]["ev_gap_pct_pot"]
            for s in suite.values()
            if "skipped" not in s.get("ev_gap", {})
        ]
        bound_ok(doc, m.group(1), max(gaps), "all-spots EV-gap bound")


def check_validation(doc: Path, suites: dict[str, dict]) -> None:
    text = doc.read_text()
    for ln, c in req_table(doc, text, "Match", "Unresolved"):
        m = re.search(r"Phase (\d+(?:\.\d+)?)", c[0])
        if not m:
            continue
        name = m.group(1)
        key = {"7": "phase7", "8": "phase8", "8.5": "phase8_5",
               "8.6": "phase8_6"}.get(name)
        if key is None or key not in suites:
            continue
        try:
            int(clean_num(c[2]))  # verdict rows only: integer count cells
        except ValueError:
            continue
        s = suites[key]
        want = {
            lab: sum(1 for v in s.values() if v["verdict"] == lab)
            for lab in ("match", "explained", "unresolved")
        }
        total = len(s)
        if int(c[1]) != total:
            fail(doc, f"Phase {name} spots: doc {c[1]} vs artifact {total}")
        for cell, lab in ((c[2], "match"), (c[3], "explained"), (c[4], "unresolved")):
            if int(clean_num(cell)) != want[lab]:
                fail(
                    doc,
                    f"Phase {name} {lab}: doc {cell} vs artifact {want[lab]}",
                )


def check_phase10(doc: Path) -> None:
    """Part 1: real-run table vs real_e2e.json, playwright log, screenshots."""
    text = doc.read_text()

    log_dir = RUNS / "phase10" / "logs"
    e2e = load("phase10/logs/real_e2e.json")

    rows = req_table(doc, text, "gpu", "exploitability", "elapsed", "nodes")
    seen = set()
    for ln, c in rows:
        jid = c[0].strip("`")
        if jid not in e2e["jobs"]:
            fail(doc, f"line {ln}: job {jid} not in real_e2e.json")
            continue
        j = e2e["jobs"][jid]
        seen.add(jid)
        if c[1] != str(j["gpu"]):
            fail(doc, f"{jid} gpu: doc {c[1]} vs {j['gpu']}")
        if c[2] != str(j["iterations_run"]):
            fail(doc, f"{jid} iterations: doc {c[2]} vs {j['iterations_run']}")
        num_close(doc, c[3], j["exploitability"], f"{jid} exploitability")
        num_close(doc, c[4].replace(" s", ""), j["elapsed_seconds"], f"{jid} elapsed")
        num_close(doc, c[5].replace(",", ""), j["num_nodes"], f"{jid} nodes")
    missing = set(e2e["jobs"]) - seen
    if missing:
        fail(doc, f"jobs missing from Part-1 results table: {sorted(missing)}")

    # Playwright suite count vs committed log.
    plog = log_dir / "playwright_e2e.log"
    if not plog.exists():
        fail(doc, "playwright_e2e.log not committed")
    else:
        m = re.search(r"(\d+) passed", plog.read_text())
        claimed = re.search(r"Playwright suite: \*\*(\d+) passed\*\*", text)
        if not m:
            fail(doc, "no 'N passed' line in playwright_e2e.log")
        elif not claimed or int(claimed.group(1)) != int(m.group(1)):
            fail(
                doc,
                f"Playwright count: doc {claimed and claimed.group(1)} vs log "
                f"{m and m.group(1)}",
            )

    # Screenshot set 01–07 must all be committed.
    for name in (
        "01-login", "02-spot-setup-estimate", "03-jobs-midrun", "04-jobs-done",
        "05-strategy-root", "06-strategy-child", "07-spot-library",
    ):
        if not (log_dir / f"{name}.png").exists():
            fail(doc, f"missing screenshot {name}.png")


def check_doc_status(doc: Path) -> None:
    """'Status: in progress' + a recorded gate result is contradictory."""
    text = doc.read_text()
    if not re.search(r"Status:\s*in progress", text):
        return
    recorded = bool(
        re.search(
            r"^\s*\*\*Gate (result|status)\s*:\s*\*\*"
            r"|^\s*\*\*Gate (result|status)\*\*\s*:",
            text,
            re.M,
        )
    )
    if not recorded:
        for m in re.finditer(r"^#{2,4}\s+Gate\b.*$", text, re.M):
            section = text[m.end():]
            stop = re.search(r"^#{1,4}\s", section, re.M)
            body = section[: stop.start()] if stop else section
            if re.search(r"passed|green|fail", body):
                recorded = True
                break
    if recorded:
        fail(
            doc,
            "'Status: in progress' but the doc already records a gate result",
        )


def main(argv: list[str]) -> int:
    suite7 = load("phase7/runs/suite.json")
    suite8 = load("phase8/runs/suite8.json")
    phase7_xeval = load("phase8/runs/xeval_phase7.json")
    suite8_5 = load("phase8_5/runs/suite8_5.json")
    float32 = load("phase8_5/runs/float32_rerun.json")
    suite8_6 = load("phase8_6/runs/suite8_6.json")

    paths = [Path(p) for p in argv[1:]] if len(argv) > 1 else sorted(DOCS.glob("*.md"))
    for doc in paths:
        name = doc.name
        if name == "PHASE7.md":
            check_phase7(doc, suite7)
        elif name == "PHASE8.md":
            check_phase8(doc, suite8, phase7_xeval, suite7)
        elif name == "PHASE8_5.md":
            check_phase8_5(doc, suite8_5, float32)
        elif name == "PHASE8_6.md":
            check_phase8_6(doc, suite8_6)
        elif name == "PHASE10.md":
            check_phase10(doc)
        elif name == "VALIDATION.md":
            check_validation(
                doc,
                {
                    "phase7": suite7,
                    "phase8": suite8,
                    "phase8_5": suite8_5,
                    "phase8_6": suite8_6,
                },
            )
        check_doc_status(doc)

    if errors:
        print("evidence guard FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"evidence guard OK: {len(paths)} docs checked")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
