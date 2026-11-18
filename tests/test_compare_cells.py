"""Negative tests for ``scripts.compare_cells``: an evaluator that omits
cells, returns nothing, or returns a partial set must surface omissions —
and the callers' ``compared == 0`` / omitted gates must fail."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.compare_cells import compare_cells, expected_cells, flatten  # noqa: E402


REF = {
    "AsKd": {"c": 1.0, "r": 0.5},
    "AhQh": {"c": 0.25, "r": 0.75},
}


def test_complete_evaluator_passes():
    compared, omitted, extras, maxd = compare_cells(REF, dict(REF))
    assert compared == 4 and omitted == [] and extras == [] and maxd == 0.0


def test_evaluator_returning_nothing_compares_zero_cells():
    compared, omitted, extras, maxd = compare_cells(REF, {})
    assert compared == 0
    assert len(omitted) == 4  # every ref cell unexplained
    assert all(o.endswith("combo missing") for o in omitted)
    assert extras == []


def test_evaluator_omitting_a_combo_is_counted():
    got = {"AsKd": dict(REF["AsKd"])}
    compared, omitted, extras, maxd = compare_cells(REF, got)
    assert compared == 2
    assert len(omitted) == 2
    assert all("AhQh" in o for o in omitted)


def test_evaluator_omitting_an_action_is_counted():
    got = {
        "AsKd": {"c": 1.0},
        "AhQh": {"c": 0.25, "r": 0.75},
    }
    compared, omitted, extras, maxd = compare_cells(REF, got)
    assert compared == 3
    assert omitted == ["AsKd/r: action missing"]


def test_partial_set_reports_exactly_the_missing_cells():
    got = {"AhQh": {"r": 0.75}}
    compared, omitted, extras, maxd = compare_cells(REF, got)
    assert compared == 1
    assert sorted(omitted) == [
        "AhQh/c: action missing",
        "AsKd/c: combo missing",
        "AsKd/r: combo missing",
    ]


def test_extra_cells_are_reported_not_silently_ignored():
    got = {c: dict(r) for c, r in REF.items()}
    got["JdJc"] = {"c": 9.0}
    got["AsKd"]["x"] = 3.0
    compared, omitted, extras, _ = compare_cells(REF, got)
    assert compared == 4
    assert sorted(extras) == ["AsKd/x", "JdJc/c"]


def test_max_abs_diff_only_over_compared_cells():
    got = {c: dict(r) for c, r in REF.items()}
    got["AsKd"]["c"] = 2.0
    compared, omitted, extras, maxd = compare_cells(REF, got)
    assert compared == 4 and maxd == 1.0


def test_nan_values_count_as_omitted_not_compared():
    for side in ("ref", "got"):
        ref = {c: dict(r) for c, r in REF.items()}
        got = {c: dict(r) for c, r in REF.items()}
        (ref if side == "ref" else got)["AsKd"]["c"] = float("nan")
        compared, omitted, extras, maxd = compare_cells(ref, got)
        assert compared == 3 and omitted == ["AsKd/c: non-finite value"]


def test_expected_cells_counts_rows_times_actions():
    from test_evwalk import RIVER_SPOT, fake_index

    index = fake_index()
    exp = expected_cells(index)
    assert set(exp) == set(index.node_at)
    total = 0
    for hist, (_p, actions) in index.node_at.items():
        total += len(index.rows_at.get(hist, {})) * len(actions)
        assert exp[hist] == len(index.rows_at.get(hist, {})) * len(actions)
    assert sum(exp.values()) == total > 0
