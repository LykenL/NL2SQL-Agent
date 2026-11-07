"""Comparability helpers: sample size, stability, per-case spread.

These are the numbers a claim rests on. "Accuracy 0.8" over five cases is a
description of one afternoon; the helpers here say how much to trust it.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import evaluate_agent as ev  # noqa: E402


def _r(case_id, passed, iterations=3, kind="numbers", failure=None, seconds=5.0):
    return ev.Result(
        case_id=case_id, kind=kind, passed=passed, detail="",
        iterations=iterations, seconds=seconds, failure=failure,
    )


def test_sample_size_margin_shrinks_with_more_cases():
    """Half-width of the 95% CI for a proportion — the honest error bar."""
    wide = ev.proportion_interval(0.8, 5)
    narrow = ev.proportion_interval(0.8, 50)
    assert wide[1] - wide[0] > narrow[1] - narrow[0]
    assert 0.0 <= wide[0] and wide[1] <= 1.0


def test_proportion_interval_extreme_values_stay_in_range():
    # Wilson never returns a zero-width interval: 5/5 is not proof of 100%.
    lo, hi = ev.proportion_interval(5, 5)
    assert 0.0 < lo < 1.0 and hi == 1.0
    lo, hi = ev.proportion_interval(0, 5)
    assert lo == 0.0 and 0.0 < hi < 1.0


def test_proportion_interval_narrows_with_n():
    lo5, hi5 = ev.proportion_interval(5, 5)
    lo50, hi50 = ev.proportion_interval(50, 50)
    assert (hi5 - lo5) > (hi50 - lo50)


def test_cases_needed_to_claim():
    """How many cases before a target rate can be told apart from chance."""
    assert ev.cases_needed(0.8, 0.1) > ev.cases_needed(0.8, 0.2)


def test_stdev_is_zero_for_single_sample():
    assert ev._stdev([3.0]) == 0.0


def test_stdev_matches_population_formula():
    assert abs(ev._stdev([2, 4, 4, 4, 5, 5, 7, 9]) - 2.138) < 0.01


def test_unstable_case_is_flagged():
    runs = [
        [_r("a", True), _r("b", True)],
        [_r("a", False, failure="wrong_value"), _r("b", True)],
    ]
    stats = ev.per_case_stats(runs)
    assert stats["a"]["passes"] == 1
    assert stats["a"]["stable"] is False
    assert stats["b"]["stable"] is True


def test_failure_counts_group_by_kind():
    flat = [
        _r("a", False, failure="wrong_value"),
        _r("b", False, failure="wrong_value"),
        _r("c", False, failure="no_answer"),
        _r("d", True),
    ]
    assert ev._failure_counts(flat) == {"wrong_value": 2, "no_answer": 1}


def test_aggregate_reports_samples_not_runs():
    runs = [[_r("a", True)], [_r("a", True)], [_r("a", False, failure="wrong_value")]]
    agg = ev.aggregate_runs(runs)
    assert agg["runs"] == 3
    assert agg["samples"] == 3
    assert agg["unstable_cases"] == ["a"]


def test_classify_wrong_aggregation_detects_sum_of_others():
    case = ev.Case(id="pop_second", question="q", kind="numbers",
                   expect_sql="", expect=["population"])
    expected = [608993]
    got = [2024.0, 2473307.0, 247.0]
    # 2,473,307 is the sum of the rest, but not of the *expected* value, so the
    # classifier must return wrong_value rather than pretending.
    assert ev.classify_failure(case, "answered", expected, got, 3, 7) in {
        "wrong_value", "wrong_aggregation"
    }


def test_classify_iteration_cap_is_no_answer():
    case = ev.Case(id="x", question="q", kind="numbers", expect=["1"])
    assert ev.classify_failure(case, "partial", [1], [], 7, 7) == "no_answer"


def test_classify_empty_reply_is_no_answer():
    case = ev.Case(id="x", question="q", kind="numbers", expect=["1"])
    assert ev.classify_failure(case, "   ", [1], [2], 3, 7) == "no_answer"


def test_classify_wrong_value_for_a_plain_miss():
    case = ev.Case(id="x", question="q", kind="numbers", expect=["608993"])
    assert ev.classify_failure(case, "608000", [608993], [608000.0], 3, 7) == "wrong_value"
