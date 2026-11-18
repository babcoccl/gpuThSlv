"""Runs in ``.venv-engine``.

Run TexasSolver console on a SpotConfig; return nodes + EV + exploitability.

Python 3.9 safe.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from holdem_solver.protocol.spot import SpotConfig

from .texas_input import spot_to_texas_input
from .texas_dump import parse_dump, dump_ev

TS_DIR = "/home/aihub/Downloads/TexasSolver-v0.2.0-Linux/TexasSolver-v0.2.0-Linux"
TS_BIN = "console_solver"
_TS_EXPL_RE = re.compile(r"Total exploitability\s+([-\d.eE]+)\s+precent")
_BUILD_RE = re.compile(r"build.*?(\d+)\s*ms", re.I | re.S)


def solve_ts(
    spot: SpotConfig,
    dump_path: str,
    threads: int = 8,
    accuracy_pct: float = 0.5,
    max_iteration: int = 4000,
    print_interval: int = 50,
    timeout_s: int = 3600,
    emit_donk: bool = False,
    x_raise_pcts: Any = None,
) -> Dict[str, Any]:
    """Returns {nodes, ev_p0, exploitability_pct, build_s, solve_s, iterations, raw_log}."""
    import os

    input_path = dump_path + ".in.txt"
    text = spot_to_texas_input(
        spot, dump_path, threads=threads, accuracy_pct=accuracy_pct,
        max_iteration=max_iteration, print_interval=print_interval,
        emit_donk=emit_donk, x_raise_pcts=x_raise_pcts,
    )
    with open(input_path, "w") as fh:
        fh.write(text)

    if os.path.exists(dump_path):
        os.remove(dump_path)  # never read output from an earlier solve
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["./console_solver", "-i", input_path],
        cwd=TS_DIR, capture_output=True, text=True, timeout=timeout_s,
    )
    wall_s = time.perf_counter() - t0
    log = proc.stdout + "\n" + proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(
            "TexasSolver exited %d: %s" % (proc.returncode, log[-2000:])
        )
    if not os.path.exists(dump_path):
        raise RuntimeError("TexasSolver produced no dump: %s" % log[-2000:])

    expls = _TS_EXPL_RE.findall(log)
    expl_pct = float(expls[-1]) if expls else None

    # tmp_log.txt holds {exploitibility, iteration, time_ms} per print
    iters = None
    time_ms = None
    tmp_log = os.path.join(TS_DIR, "tmp_log.txt")
    if os.path.exists(tmp_log):
        try:
            with open(tmp_log) as fh:
                records = [json.loads(line) for line in fh if line.strip()]
            if records:
                iters = records[-1].get("iteration")
                time_ms = records[-1].get("time_ms")
        except Exception:
            pass

    nodes = parse_dump(dump_path)
    ev = dump_ev(
        dump_path, pot=spot.pot,
        board=list(spot.board), range0=spot.range0, range1=spot.range1,
    )
    return {
        "nodes": nodes,
        "ev_p0": ev,
        "exploitability_pct": expl_pct,
        "build_s": None,
        "solve_s": wall_s,
        "solve_time_ms_reported": time_ms,
        "iterations": iters,
        "exit_code": proc.returncode,
        "log_tail": log[-3000:],
        "input": text,
    }
