"""Compared-cell accounting for the EV validation scripts.

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


def compare_cells(ref, got):
    """Compare ``got`` (evaluator output) cell-by-cell against ``ref``.

    ``ref`` and ``got`` are nested ``{combo: {action: float}}`` tables.

    Returns ``(compared, omitted, extras, max_abs_diff)``:
    - ``compared``: cells present in both maps (the only cells contributing
      to ``max_abs_diff``).
    - ``omitted``: ``["combo/action: reason", ...]`` for every ref cell the
      evaluator did not produce ("combo missing" / "action missing").
    - ``extras``: ``["combo/action", ...]`` cells ``got`` produced that ref
      has no expectation for.
    """
    compared = 0
    max_diff = 0.0
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
            if d > max_diff:
                max_diff = d
    for combo, row in got.items():
        ref_row = ref.get(combo)
        for action in row:
            if ref_row is None or action not in ref_row:
                extras.append("%s/%s" % (combo, action))
    return compared, omitted, extras, max_diff


def expected_cells(index):
    """Per-player-node cell count computed independently from the tree:
    ``Σ hist Σ combo len(actions)`` — no evaluator involved."""
    out = {}
    for hist, (_player, actions) in index.node_at.items():
        out[hist] = len(index.rows_at.get(hist, {})) * len(actions)
    return out
