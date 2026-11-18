#!/usr/bin/env python3
"""Runs in ``.venv-engine`` and ``.venv-api``.

Phase 10 evidence guard: docs result tables vs committed suite JSONs.

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
import subprocess
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
    # float slack proportional to the tolerance — a fixed floor (even
    # 1e-12) would exceed half the displayed unit for cells like 6.4e-14.
    if abs(v - ref) > tol + tol * 1e-6 + abs(ref) * 1e-15:
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


def check_rows(
    doc: Path,
    rows: list[tuple[int, list[str]]],
    expected_keys: list,
    what: str,
    key=lambda c: c[0].strip("`"),
) -> None:
    """The table's row set must equal ``expected_keys`` — no missing, extra
    or reordered rows. Order is enforced whenever ``expected_keys`` is an
    ordered list; pass a set to check membership only."""
    got = [key(c) for _ln, c in rows]
    expected = list(expected_keys)
    missing = [k for k in expected if k not in got]
    extra = [k for k in got if k not in expected]
    if missing:
        fail(doc, f"{what}: rows missing from table: {missing}")
    if extra:
        fail(doc, f"{what}: unexpected rows in table: {extra}")
    if not missing and not extra and got != expected:
        fail(doc, f"{what}: row order {got} != artifact order {expected}")


def check_phase7(doc: Path, suite: dict) -> None:
    text = doc.read_text()
    rows = req_table(doc, text, "our EV", "verdict")
    if not rows:
        return
    check_rows(doc, rows, list(suite), "results table")
    for ln, c in rows:
        spot = c[0]
        if spot not in suite:
            continue
        s = suite[spot]
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
    check_rows(doc, rows, list(suite), "phase8 results table")
    for ln, c in rows:
        spot = c[0]
        s = suite.get(spot)
        if s is None:
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
        check_rows(doc, table_rows, list(src), "xeval table")
        for ln, c in table_rows:
            spot = c[0]
            s = src.get(spot)
            if s is None:
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
    gap_rows = req_table(doc, text, "EV gap %pot")
    check_rows(
        doc, gap_rows,
        [
            k for k, s in suite.items()
            if "ev_gap" in s and "skipped" not in s["ev_gap"]
        ],
        "EV-gap table",
    )
    for ln, c in gap_rows:
        spot = c[0]
        s = suite.get(spot)
        if s is None or "ev_gap" not in s:
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

    f32_rows = req_table(doc, text, "Δexploitability")
    check_rows(doc, f32_rows, list(float32["spots"]), "float32 table")
    for ln, c in f32_rows:
        spot = c[0]
        s = float32["spots"].get(spot)
        if s is None:
            continue
        dev = abs(float(s["float64"]["ev_p0"]) - float(s["float32"]["ev_p0"]))
        dexpl = abs(
            float(s["float64"]["exploitability_pct"])
            - float(s["float32"]["exploitability_pct"])
        )
        num_close(doc, c[1], dev, f"{spot} f32 dEV chips")
        num_close(doc, c[2], dexpl, f"{spot} f32 dexpl pp")

    xeval_rows = req_table(doc, text, "xeval ts-in-ours")
    # The table intentionally covers only the three turn re-run spots.
    check_rows(
        doc, xeval_rows,
        ["turn-narrow-a", "turn-narrow-b", "w-turn-cap0"],
        "phase8_5 xeval table",
    )
    for ln, c in xeval_rows:
        spot = c[0]
        s = suite.get(spot)
        if s is None:
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
    check_rows(doc, rows, list(suite), "phase8_6 table")
    for ln, c in rows:
        spot = c[0]
        s = suite.get(spot)
        if s is None:
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
    rows = req_table(doc, text, "Match", "Unresolved")
    verdict_rows = []
    for ln, c in rows:
        try:
            int(clean_num(c[2]))
        except (ValueError, IndexError):
            continue
        verdict_rows.append((ln, c))
    check_rows(
        doc, verdict_rows,
        ["Phase 7", "Phase 8", "Phase 8.5", "Phase 8.6"],
        "VALIDATION verdict table",
        key=lambda c: re.search(r"Phase \d+(?:\.\d+)?", c[0]).group(0)
        if re.search(r"Phase \d+(?:\.\d+)?", c[0]) else c[0],
    )
    for ln, c in verdict_rows:
        m = re.search(r"Phase (\d+(?:\.\d+)?)", c[0])
        if not m:
            continue
        m = re.search(r"Phase (\d+(?:\.\d+)?)", c[0])
        name = m.group(1)
        key = {"7": "phase7", "8": "phase8", "8.5": "phase8_5",
               "8.6": "phase8_6"}.get(name)
        if key is None or key not in suites:
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
    check_rows(doc, rows, list(e2e["jobs"]), "Part-1 results table")
    for ln, c in rows:
        jid = c[0].strip("`")
        if jid not in e2e["jobs"]:
            continue
        j = e2e["jobs"][jid]
        if c[1] != str(j["gpu"]):
            fail(doc, f"{jid} gpu: doc {c[1]} vs {j['gpu']}")
        if c[2] != str(j["iterations_run"]):
            fail(doc, f"{jid} iterations: doc {c[2]} vs {j['iterations_run']}")
        num_close(doc, c[3], j["exploitability"], f"{jid} exploitability")
        num_close(doc, c[4].replace(" s", ""), j["elapsed_seconds"], f"{jid} elapsed")
        num_close(doc, c[5].replace(",", ""), j["num_nodes"], f"{jid} nodes")

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


def check_phase10_2(doc: Path) -> None:
    """Scale table vs committed scale-10-2.json (re-measured columns:
    nodes, build s, solve s, export s, iterations, exploitability,
    stop reason, first/warm /node, /tree, TTFR); the before/after /node
    table vs scale-10-1.json (Phase 10.1 baseline) and scale-10-2.json."""
    text = doc.read_text()
    scale = load("phase10/logs/scale-10-2.json")
    before = load("phase10/logs/scale-10-1.json")
    # canonical before-numbers: cold re-measure at the Phase-10.1 merge
    # commit (scale-10-1.json itself was sampled under load)
    cold = load("phase10/logs/scale-10-1-cold.json")
    cold_mean = {n: r["mean_ms"] for n, r in cold["runs"].items()}
    by_name = {r["name"]: r for r in scale}
    before_by_name = {r["name"]: r for r in before}
    rows = req_table(doc, text, "build s", "first /node", exclude=("wall",))
    check_rows(doc, rows, [r["name"] for r in scale], "scale table")
    for ln, c in rows:
        name = c[0].strip("`")
        r = by_name.get(name)
        if r is None:
            continue
        num_close(doc, c[1].replace(",", ""), r["nodes"], f"{name} nodes")
        num_close(doc, c[2], r["build_s"], f"{name} build s")
        num_close(doc, c[3], r["solve_s"], f"{name} solve s")
        num_close(doc, c[4], r["export_s"], f"{name} export s")
        num_close(doc, c[5], r["iterations"], f"{name} iterations")
        # doc column is expl (% pot); all scale spots use pot=20.
        num_close(doc, c[6], r["exploitability"] / 20 * 100, f"{name} expl (% pot)")
        if c[7] != r["stop_reason"]:
            fail(doc, f"{name} stop reason: doc {c[7]!r} vs {r['stop_reason']!r}")
        root = r["calls"][0]
        if root.get("history") != "root" or "first_node_ms" not in root:
            fail(doc, f"{name}: artifact calls[0] has no root first/warm /node")
            continue
        num_close(doc, c[8], root["first_node_ms"], f"{name} first /node ms")
        num_close(doc, c[9], root["warm_node_ms"], f"{name} warm /node ms")
        num_close(doc, c[10], root["tree_ms"], f"{name} /tree ms")
        num_close(doc, c[11], r["ttfr_ms"], f"{name} TTFR ms")

    ba = req_table(doc, text, "before", "after")
    check_rows(doc, ba, [r["name"] for r in scale], "before/after table")
    for ln, c in ba:
        name = c[0].strip("`")
        b = before_by_name.get(name)
        r = by_name.get(name)
        if b is None:
            fail(doc, f"line {ln}: spot {name!r} not in scale-10-1.json")
            continue
        num_close(
            doc, c[1].replace(" ms", "").replace(",", ""),
            cold_mean.get(name, b["calls"][0]["node_ms"]),
            f"{name} first /node before",
        )
        if r is not None:
            num_close(
                doc, c[2].replace(" ms", ""), r["calls"][0]["first_node_ms"],
                f"{name} first /node after",
            )


def check_phase10_3(doc: Path) -> None:
    """evec_all_nodes table vs committed evec_all_nodes.json — and the
    artifact's own compared/expected accounting: every check must have
    compared > 0 cells, compared == expected, and zero unexplained
    omissions, extras, or ref-side missing cells."""
    text = doc.read_text()
    report = load("phase10/logs/evec_all_nodes.json")
    rows = req_table(doc, text, "histories", "persisted vs live")
    check_rows(doc, rows, list(report), "evec_all_nodes table")
    for ln, c in rows:
        name = c[0].strip("`")
        r = report.get(name)
        if r is None:
            continue
        num_close(doc, c[1].replace(",", ""), r["hists"], f"{name} hists")
        agg = r["checks"]["aggregate_edge_value"]
        split = r["checks"]["parity_split"]
        pers = r["checks"]["persisted_vs_live_evec"]
        exact = r["checks"]["exact_per_combo"]
        num_close(doc, c[2], agg["max_abs_diff"], f"{name} agg")
        num_close(doc, c[3], split["max_abs_diff"], f"{name} split")
        m = re.match(r"([\d.eE+-]+)\s+over\s+([\d,]+)\s+cells", c[4])
        if m is None:
            fail(doc, f"line {ln}: exact cell not 'X over N cells': {c[4]!r}")
        else:
            num_close(doc, m.group(1), exact["max_abs_diff"],
                      f"{name} exact")
            num_close(doc, m.group(2).replace(",", ""), exact["compared"],
                      f"{name} exact cells")
        num_close(doc, c[5], pers["max_abs_diff"], f"{name} persisted")
        for label, chk in (("persisted_vs_live_evec", pers),
                           ("exact_per_combo", exact)):
            for prob in (
                chk["compared"] == 0 and "compared zero cells",
                chk["compared"] != chk["expected_cells"]
                and f"compared {chk['compared']} != expected "
                f"{chk['expected_cells']}",
                chk.get("omitted_count") and "unexplained omissions",
                chk.get("extras_count") and "extra cells",
                chk.get("ref_missing") and "reference missing cells",
            ):
                if prob:
                    fail(doc, f"{name} {label}: {prob}")
        # Phase 10.6: one per-cell rule |diff| <= 1e-12 * max(1, |ref|)
        # <=> max_rel_diff <= 1e-12. The JSON also reports the
        # worst-absolute and worst-relative cells separately; assert both
        # are present and consistent with the maxima.
        for label, chk in r["checks"].items():
            rel = chk.get("max_rel_diff")
            if rel is None:
                fail(doc, f"{name} {label}: max_rel_diff missing")
            elif rel > 1e-12:
                fail(doc, f"{name} {label}: rel {rel} > 1e-12")
            wa = chk.get("worst_abs_cell")
            wr = chk.get("worst_rel_cell")
            if wa is None or wr is None:
                fail(doc, f"{name} {label}: worst_abs_cell/worst_rel_cell missing")
            else:
                if wa.get("diff") != chk["max_abs_diff"]:
                    fail(doc, f"{name} {label}: worst_abs_cell.diff != max_abs_diff")
                if wr.get("rel") != rel:
                    fail(doc, f"{name} {label}: worst_rel_cell.rel != max_rel_diff")


SMOKE_LOG_RE = re.compile(
    r"^scripts/release/smoke-results/"
    r"(?:\d{8}-\d{6}|<ts>)-(?:T[1-4]|nosys|<tier>)-(?:fake|real|<engine>)(?:-cutover)?\.log$"
)
GATE_LOG_RE = re.compile(r"^scripts/release/smoke-results/\d{8}-\d{6}-run_all_tests\.log$")
REPO_PATH_RE = re.compile(
    r"^(?:docs|scripts|tests|deploy|requirements|holdem_solver|ui|third_party)/[\w./@<>-]+(?:::\w+)?$"
    r"|^(?:RUNBOOK|README|AGENTS)\.md$"
)
SUITE_RE = re.compile(r"(engine|api) suite (\d+) passed / (\d+) skipped")


def _tracked() -> set[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    return set(out.stdout.split())


def check_release_docs(doc: Path, tracked: set[str] | None = None) -> None:
    """PHASE11*.md: cited paths exist and are committed, smoke logs follow
    <ts>-<tier>-<engine>.log and their header matches the name, and every
    suite count equals the cited run_all_tests gate log."""
    tracked = _tracked() if tracked is None else tracked
    text = doc.read_text()
    gate_counts: dict[str, tuple[int, int]] = {}
    for tok in re.findall(r"`([^`\s]+)`", text):
        if not REPO_PATH_RE.match(tok) or not re.search(r"\.\w+(?:::\w+)?$", tok):
            continue  # only files (build-output dirs like ui/dist are not committed)
        path, _, func = tok.partition("::")
        if "smoke-results/" in path and not GATE_LOG_RE.match(path) and not SMOKE_LOG_RE.match(path):
            fail(doc, f"smoke log {path} not named <ts>-<tier>-<engine>.log")
            continue
        if "<" in path or "*" in path:
            continue
        if path not in tracked and not any(t.startswith(path.rstrip("/") + "/") for t in tracked):
            fail(doc, f"cites {path}, which is not a committed file")
            continue
        full = ROOT / path
        if func and (f"def {func}(" if path.endswith(".py") else f"{func}()") not in full.read_text():
            fail(doc, f"cites {tok}, but {path} has no {func}()")
        m = re.search(r"-(T[1-4]|nosys)-(fake|real)(?:-cutover)?\.log$", path)
        if m and "smoke-results/" in path:
            log = full.read_text()
            if f"# tier:   {m.group(1)}" not in log or f"# engine: {m.group(2)}" not in log:
                fail(doc, f"{path}: header tier/engine do not match the file name")
            if "=== PASS ===" not in log:
                fail(doc, f"{path}: no PASS recorded")
        if GATE_LOG_RE.match(path):
            log = full.read_text()
            for label, marker in (("engine", "=== engine suite"), ("api", "=== api suite")):
                start = log.find(marker)
                seg = log[start:] if start >= 0 else ""
                nm = re.search(r"(\d+) passed(?:, (\d+) skipped)?", seg)
                if nm:
                    gate_counts[label] = (int(nm.group(1)), int(nm.group(2) or 0))
    claims = SUITE_RE.findall(text)
    if claims and not gate_counts:
        fail(doc, "states suite counts but cites no committed *-run_all_tests.log")
    for label, passed, skipped in claims:
        got = gate_counts.get(label)
        if got and got != (int(passed), int(skipped)):
            fail(doc, f"{label} suite {passed} passed / {skipped} skipped != gate log {got[0]}/{got[1]}")


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
        elif name == "PHASE10_2.md":
            check_phase10_2(doc)
        elif name == "PHASE10_3.md":
            check_phase10_3(doc)
        elif name.startswith("PHASE11"):
            check_release_docs(doc)
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
