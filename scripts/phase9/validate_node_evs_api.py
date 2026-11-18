"""Phase 9/10.4 EV validation, API-venv stage: evec + persisted paths.

Runs in ``.venv-api`` — never in the engine venv (see the import-boundary
rule in AGENTS.md). Consumes ``runs/solved_nodes_<spot>.json`` written by
the engine stage (``scripts.phase9.validate_node_evs``) plus the existing
``runs/ev_validation_<spots>.json`` artifact, and merges the API-path
results back into it under ``_manifest.stages.api``:

- (a) ``evec.node_action_evs`` vs ``evwalk.node_action_evs`` per node,
  per combo per action — with compared/omitted accounting against the
  expected cell count derived from the tree itself
  (``scripts.compare_cells``);
- (b) the persisted ``strategy-*.evs.npz`` layout round-tripped through
  ``load_node_evs`` exactly as ``/node`` serves it, plus the served hand
  EVs through ``node_values``.

Usage: .venv-api/bin/python -m scripts.phase9.validate_node_evs_api \
           [spot ...] [--via {evec,persisted,all}]
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from holdem_solver.protocol.cards import combo_str, parse_cards, parse_range  # noqa: E402
from holdem_solver.protocol.evwalk import (  # noqa: E402
    TreeIndex,
    dealt_board,
    node_action_evs as our_action_evs,
    reach_at,
)
from holdem_solver.api import evec  # noqa: E402
from holdem_solver.api.nodes import (  # noqa: E402
    export_node_evs,
    load_node_evs,
    node_values,
)
from scripts.compare_cells import compare_cells, expected_cells, flatten  # noqa: E402
from scripts.phase8.spots import SPOTS  # noqa: E402
from scripts.phase9.validate_node_evs import (  # noqa: E402
    CAP0_RIVER,
    FULL_RUN,
    TOL_CHIPS,
    TOL_FRAC_OF_POT,
    out_path,
    solved_nodes_path,
    tol,
)

RUNS = os.path.join(os.path.dirname(__file__), "runs")


def our_index(nodes):
    by_node = {}
    for node in nodes:
        hist = tuple(node["history"])
        by_node.setdefault((node["player"], hist), {}).update(node["strategy"])
    return TreeIndex(by_node)


def _hist_sections(hist):
    """Infoset-key history sections: action runs comma-joined; river
    edges are bare-card sections (``river:As`` -> ``As``)."""
    sections = []
    run = []
    for elem in hist:
        if elem.startswith("river:"):
            if run:
                sections.append(",".join(run))
                run = []
            sections.append(elem[6:])
        else:
            run.append(elem)
    if run:
        sections.append(",".join(run))
    return sections


def persisted_roundtrip(index, spot):
    """Write ``export_node_evs`` output to a real ``*.evs.npz`` — the
    scheduler's layout including the payload checksum — and read it back
    through ``load_node_evs``, exactly as ``/node`` serves it. Returns
    ``evs`` keyed by ``hist``."""
    import tempfile
    import zlib

    import numpy as np

    evs, masses = export_node_evs(index, spot)
    keys, actions, flat, mass = [], [], [], []
    for (player, hist), combos in sorted(evs.items()):
        for hand, a_evs in sorted(combos.items()):
            acts = list(a_evs)
            keys.append(
                "p%d|%s|%s" % (player, hand, "|".join(_hist_sections(hist)))
            )
            actions.append(",".join(acts))
            flat.extend(float(a_evs[a]) for a in acts)
            mass.append(float(masses.get((player, hist), {}).get(hand, 0.0)))
    offsets = np.cumsum([0] + [len(a.split(",")) for a in actions])
    ev = np.array(flat, dtype=np.float64)
    mass_a = np.array(mass, dtype=np.float64)
    tmp = tempfile.NamedTemporaryFile(suffix=".evs.npz", delete=False)
    tmp.close()
    np.savez(
        tmp.name,
        keys=np.array([k.encode() for k in keys], dtype=np.bytes_),
        actions=np.array([a.encode() for a in actions], dtype=np.bytes_),
        offsets=offsets,
        ev=ev,
        opp_mass=mass_a,
        crc32=np.int64(zlib.crc32(ev.tobytes()) ^ zlib.crc32(mass_a.tobytes())),
        iteration=np.int64(0),
    )
    try:
        loaded = load_node_evs(tmp.name, keys)
    finally:
        os.unlink(tmp.name)
    assert loaded is not None, "persisted round-trip npz failed to load"
    return loaded[0]


def served_hand_evs(index, spot, hist, node_evs):
    """``node_values`` hand EVs per combo as ``/node`` aggregates them
    from the persisted table."""
    resp = node_values(index, spot, hist, combos=True, node_evs=node_evs)
    return {row["combo"]: row["ev"] for row in resp["hands"]}


def git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        ).decode().strip()
    except Exception:
        return "unknown"


def main(names=None, via="all"):
    spots = [s for s in CAP0_RIVER if names is None or s.name in names]
    if names is not None:
        missing = [n for n in names if n not in {s.name for s in spots}]
        if missing:
            print("unknown spot(s): %s" % ", ".join(missing))
            sys.exit(2)
    run_names = [s.name for s in spots]
    out = out_path(run_names)
    if not os.path.exists(out):
        print(
            "%s missing — run the engine stage first:\n"
            "  .venv-engine/bin/python -m scripts.phase9.validate_node_evs"
            % out
        )
        sys.exit(2)
    artifact = json.load(open(out))
    results = artifact["spots"]
    failures = 0
    for spot in spots:
        name = spot.name
        if name not in results:
            print("=== %s: no engine-stage result in %s — skipping"
                  % (name, out))
            failures += 1
            continue
        print("=== %s (api stage) ===" % name, flush=True)
        solved_file = solved_nodes_path(name)
        if not os.path.exists(solved_file):
            print("  %s missing — run the engine stage first" % solved_file)
            failures += 1
            continue
        solved = json.load(open(solved_file))
        own_index = our_index(solved["nodes"])
        pot = spot.pot
        board_ids = tuple(parse_cards(spot.board))
        dead_ids = tuple(parse_cards(spot.dead_cards or ()))
        blocked = board_ids + dead_ids
        w0 = {combo_str(c): w for c, w in parse_range(spot.range0).items()}
        w1 = {combo_str(c): w for c, w in parse_range(spot.range1).items()}
        limit = tol(pot)

        live_compared = live_expected = live_ref_missing = 0
        live_omitted: list = []
        live_extras: list = []
        live_max = 0.0
        persist_compared = persist_expected = persist_ref_missing = 0
        persist_omitted: list = []
        persist_extras: list = []
        persist_max = 0.0
        served_compared = served_expected = 0
        served_omitted: list = []
        served_max = 0.0
        expected_per_hist = expected_cells(own_index)

        evs_persisted = (
            persisted_roundtrip(own_index, spot.to_dict())
            if via in ("persisted", "all") else {}
        )
        for (player, hist), _rows in sorted(own_index.by_node.items()):
            node_exp = expected_per_hist[hist]
            reach0, reach1 = reach_at(own_index, hist, w0, w1, blocked)
            board_now = dealt_board(spot.board, hist)
            ref = our_action_evs(own_index, hist, reach0, reach1,
                                 board_now, pot)
            node_rows = own_index.rows_at[hist]
            ref_hand = {
                combo: sum(
                    float(node_rows.get(combo, {}).get(a, 0.0)) * float(v)
                    for a, v in ref_evs.items()
                )
                for combo, ref_evs in ref.items()
            }
            if via in ("evec", "all"):
                live_expected += node_exp
                live_ref_missing += node_exp - len(flatten(ref))
                got = evec.node_action_evs(own_index, hist, reach0, reach1,
                                           board_now, pot)
                c, o, e, m = compare_cells(ref, got)
                live_compared += c
                live_omitted += o
                live_extras += e
                live_max = max(live_max, m)
            if via in ("persisted", "all"):
                persist_expected += node_exp
                persist_ref_missing += node_exp - len(flatten(ref))
                prow = evs_persisted.get(hist, {})
                c, o, e, m = compare_cells(ref, prow)
                persist_compared += c
                persist_omitted += o
                persist_extras += e
                persist_max = max(persist_max, m)
                served = served_hand_evs(own_index, spot.to_dict(), hist,
                                         evs_persisted)
                for combo, rh in ref_hand.items():
                    served_expected += 1
                    sv = served.get(combo)
                    if sv is None:
                        served_omitted.append(
                            "%s/%s: served ev missing" % (hist, combo))
                        continue
                    served_compared += 1
                    served_max = max(served_max, abs(sv - rh))
        print("  evec: %d/%d cells omit=%d max|d|=%r; persisted: %d/%d "
              "omit=%d max|d|=%r; served: %d/%d omit=%d max|d|=%r"
              % (live_compared, live_expected, len(live_omitted), live_max,
                 persist_compared, persist_expected, len(persist_omitted),
                 persist_max, served_compared, served_expected,
                 len(served_omitted), served_max), flush=True)

        evec_ok = (
            via not in ("evec", "all")
            or (live_max <= limit and live_compared == live_expected
                and live_compared > 0 and not live_omitted
                and not live_extras and live_ref_missing == 0)
        )
        persist_ok = (
            via not in ("persisted", "all")
            or (persist_max <= limit and persist_compared == persist_expected
                and persist_compared > 0 and not persist_omitted
                and not persist_extras and persist_ref_missing == 0
                and served_max <= limit and served_compared == served_expected
                and served_compared > 0 and not served_omitted)
        )
        results[name]["node_evs_api_evec"] = {
            "expected_cells": live_expected,
            "compared": live_compared,
            "ref_missing": live_ref_missing,
            "omitted_count": len(live_omitted),
            "extras_count": len(live_extras),
            "max_abs_diff": live_max,
            "ok": evec_ok,
            "path": "evec.node_action_evs (live vectorized walk, api venv)"}
        results[name]["node_evs_api_persisted"] = {
            "expected_cells": persist_expected,
            "compared": persist_compared,
            "ref_missing": persist_ref_missing,
            "omitted_count": len(persist_omitted),
            "extras_count": len(persist_extras),
            "served_compared": served_compared,
            "served_expected": served_expected,
            "served_omitted_count": len(served_omitted),
            "max_abs_diff": persist_max,
            "served_max_abs_diff": served_max,
            "ok": persist_ok,
            "path": "export_node_evs -> strategy-*.evs.npz -> load_node_evs"
                    " -> node_values (as /node serves, api venv)"}
        results[name]["ok"] = results[name]["ok"] and evec_ok and persist_ok
        if not results[name]["ok"]:
            # engine-stage failures are preserved in the artifact and must
            # drive the exit status too, not just the api checks above
            failures += 1

    manifest = artifact["_manifest"]
    manifest.setdefault("stages", {})["api"] = {
        "git_commit": git_commit(),
        "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "via": via,
    }
    json.dump(artifact, open(out, "w"), indent=1)
    print("\nwrote %s; %d spot(s) failed" % (out, failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    argv = list(sys.argv[1:])
    via = "all"
    if "--via" in argv:
        i = argv.index("--via")
        via = argv[i + 1]
        del argv[i: i + 2]
        if via not in ("evec", "persisted", "all"):
            print("--via must be evec|persisted|all")
            sys.exit(2)
    main(argv or None, via=via)
