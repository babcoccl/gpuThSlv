"""Runs in ``.venv-engine`` and ``.venv-api``.

Negative tests for ``scripts.compare_cells``: an evaluator that omits
cells, returns nothing, or returns a partial set must surface omissions —
and the callers' ``compared == 0`` / omitted gates must fail. Plus the
single per-cell tolerance rule ``|diff| <= 1e-12 * max(1, |ref|)`` and
the worst-absolute / worst-relative cell reporting.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.compare_cells import REL_TOL, compare_cells, expected_cells, flatten, rel_diff  # noqa: E402


REF = {
    "AsKd": {"c": 1.0, "r": 0.5},
    "AhQh": {"c": 0.25, "r": 0.75},
}


def test_complete_evaluator_passes():
    compared, omitted, extras, wa, wr = compare_cells(REF, dict(REF))
    assert compared == 4 and omitted == [] and extras == []
    assert wa["diff"] == 0.0 and wr["rel"] == 0.0


def test_evaluator_returning_nothing_compares_zero_cells():
    compared, omitted, extras, wa, wr = compare_cells(REF, {})
    assert compared == 0
    assert len(omitted) == 4  # every ref cell unexplained
    assert all(o.endswith("combo missing") for o in omitted)
    assert extras == []
    assert wa is None and wr is None


def test_evaluator_omitting_a_combo_is_counted():
    got = {"AsKd": dict(REF["AsKd"])}
    compared, omitted, extras, _wa, _wr = compare_cells(REF, got)
    assert compared == 2
    assert len(omitted) == 2
    assert all("AhQh" in o for o in omitted)


def test_evaluator_omitting_an_action_is_counted():
    got = {
        "AsKd": {"c": 1.0},
        "AhQh": {"c": 0.25, "r": 0.75},
    }
    compared, omitted, extras, _wa, _wr = compare_cells(REF, got)
    assert compared == 3
    assert omitted == ["AsKd/r: action missing"]


def test_partial_set_reports_exactly_the_missing_cells():
    got = {"AhQh": {"r": 0.75}}
    compared, omitted, extras, _wa, _wr = compare_cells(REF, got)
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
    compared, omitted, extras, _wa, _wr = compare_cells(REF, got)
    assert compared == 4
    assert sorted(extras) == ["AsKd/x", "JdJc/c"]


def test_worst_abs_cell_only_over_compared_cells():
    got = {c: dict(r) for c, r in REF.items()}
    got["AsKd"]["c"] = 2.0
    compared, omitted, extras, wa, wr = compare_cells(REF, got)
    assert compared == 4 and wa["diff"] == 1.0
    assert wa["combo"] == "AsKd" and wa["action"] == "c"
    assert wa["got"] == 2.0 and wa["ref"] == 1.0 and wa["rel"] == 1.0


def test_single_rule_large_ref_cell():
    """``|diff| <= 1e-12 * max(1, |ref|)``: a big-absolute diff at a big
    ref passes the rule; the same diff at |ref| ~ 1 fails it."""
    assert rel_diff(57000.0 + 2.0e-10, 57000.0) <= REL_TOL  # ~3.5e-15
    assert rel_diff(1.0 + 2.0e-10, 1.0) > REL_TOL          # ~2.0e-10


def test_worst_abs_and_worst_rel_are_different_cells():
    """The worst-absolute and worst-relative cells are usually different
    cells; both are reported with value/ref/relative."""
    ref = {"a": {"x": 57000.0}, "b": {"x": 2.0}}
    got = {"a": {"x": 57000.0 + 2.0e-10}, "b": {"x": 2.0 + 6.0e-12}}
    compared, omitted, extras, wa, wr = compare_cells(ref, got)
    assert compared == 2 and not omitted and not extras
    assert wa["combo"] == "a" and wa["diff"] == pytest.approx(2.0e-10, rel=5e-2)
    assert wa["rel"] < 1e-14  # ~3.5e-15
    assert wr["combo"] == "b" and wr["rel"] == pytest.approx(3.0e-12, rel=1e-3)
    assert wr["diff"] < wa["diff"]


def test_nan_values_count_as_omitted_not_compared():
    for side in ("ref", "got"):
        ref = {c: dict(r) for c, r in REF.items()}
        got = {c: dict(r) for c, r in REF.items()}
        (ref if side == "ref" else got)["AsKd"]["c"] = float("nan")
        compared, omitted, extras, wa, wr = compare_cells(ref, got)
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
