"""Runs in ``.venv-engine`` and ``.venv-api``.

Compared-cell accounting for the EV validation scripts.

A validation pass that silently skips cells reports a misleading
``max_abs_diff``: an evaluator that returns nothing produces
``max_abs_diff == 0`` over zero cells. ``compare_cells`` splits compared
from omitted cells and names the reason for each omission, so callers can
fail on any unexplained gap and refuse a zero-cell pass.

Cell tables are nested ``{combo: {action: value}}`` mappings, the shape
``evec.node_action_evs`` / ``evwalk.node_action_evs`` return per node
(aggregated across nodes by the callers into one table).
"""
from __future__ import annotations

import math


def flatten(table):
    """``{(combo, action): value}`` for a nested cell table."""
    return {
        (combo, action): value
        for combo, row in table.items()
        for action, value in row.items()
    }


REL_TOL = 1e-12


def rel_diff(got, ref):
    """The single per-cell tolerance rule: ``|diff| / max(1, |ref|)``;
    a cell passes iff this is <= ``REL_TOL``."""
    return abs(got - ref) / max(1.0, abs(ref))


def compare_cells(ref, got):
    """Compare ``got`` (evaluator output) cell-by-cell against ``ref``.

    ``ref`` and ``got`` are nested ``{combo: {action: float}}`` tables.

    Returns ``(compared, omitted, extras, worst_abs, worst_rel)``:
    - ``compared``: cells present in both maps (the only cells
      contributing to the worst-cell records).
    - ``omitted``: ``["combo/action: reason", ...]`` for every ref cell
      the evaluator did not produce ("combo missing" / "action missing").
    - ``extras``: ``["combo/action", ...]`` cells ``got`` produced that
      ref has no expectation for.
    - ``worst_abs`` / ``worst_rel``: the compared cells with the largest
      absolute diff and the largest relative diff respectively (they are
      usually different cells), each
      ``{"combo", "action", "got", "ref", "diff", "rel"}`` or ``None``
      when nothing was compared.
    """
    compared = 0
    worst_abs = None
    worst_rel = None
    omitted = []
    extras = []
    for combo, row in ref.items():
        got_row = got.get(combo)
        for action, rv in row.items():
            if got_row is None:
                omitted.append("%s/%s: combo missing" % (combo, action))
                continue
            if action not in got_row:
                omitted.append("%s/%s: action missing" % (combo, action))
                continue
            if not (math.isfinite(rv) and math.isfinite(got_row[action])):
                omitted.append("%s/%s: non-finite value" % (combo, action))
                continue
            compared += 1
            d = abs(got_row[action] - rv)
            rel = d / max(1.0, abs(rv))
            cell = {
                "combo": combo, "action": action,
                "got": got_row[action], "ref": rv,
                "diff": d, "rel": rel,
            }
            if worst_abs is None or d > worst_abs["diff"]:
                worst_abs = cell
            if worst_rel is None or rel > worst_rel["rel"]:
                worst_rel = cell
    for combo, row in got.items():
        ref_row = ref.get(combo)
        for action in row:
            if ref_row is None or action not in ref_row:
                extras.append("%s/%s" % (combo, action))
    return compared, omitted, extras, worst_abs, worst_rel


def expected_cells(index):
    """Per-player-node cell count computed independently from the tree:
    ``Σ hist Σ combo len(actions)`` — no evaluator involved."""
    out = {}
    for hist, (_player, actions) in index.node_at.items():
        out[hist] = len(index.rows_at.get(hist, {})) * len(actions)
    return out
