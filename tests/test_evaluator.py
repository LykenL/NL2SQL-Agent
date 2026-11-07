"""Evaluator logic: expectation resolution, matching, and reporting.

No model calls happen here — the cases run through the agent only in the CLI
script, so this suite stays free and fast.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import evaluate_agent as ev  # noqa: E402


class FakeSource:
    """Stands in for MartSource: returns canned rows for expect_sql."""

    def __init__(self, rows):
        self.rows = rows
        self.sql = []
        import pandas as pd

        self._pd = pd

    def run(self, sql, max_rows=None):
        self.sql.append(sql)
        return self._pd.DataFrame(self.rows)


def test_expectation_comes_from_sql_not_the_case():
    case = ev.Case(id="x", question="q", kind="numbers",
                   expect_sql="SELECT population FROM t", expect=["population"])
    src = FakeSource([{"population": 995574}, {"population": 608993}])
    assert ev.resolve_expectation(case, src) == [995574, 608993]
    assert "SELECT population" in src.sql[0]


def test_expectation_uses_named_column_not_first_column():
    case = ev.Case(id="x", question="q", kind="numbers",
                   expect_sql="SELECT name, population FROM t", expect=["population"])
    src = FakeSource([{"name": "Stockholm", "population": 995574}])
    assert ev.resolve_expectation(case, src) == [995574]


def test_unnamed_column_falls_back_to_first():
    case = ev.Case(id="x", question="q", kind="numbers",
                   expect_sql="SELECT COUNT(*) FROM t", expect=["c"])
    src = FakeSource([{"f0_": 12}])
    assert ev.resolve_expectation(case, src) == [12]


def test_no_expect_sql_returns_literal_expectation():
    case = ev.Case(id="x", question="q", kind="numbers", expect=[1, 2])
    assert ev.resolve_expectation(case, FakeSource([])) == [1, 2]


def test_numbers_match_exact_and_float_noise():
    # BigQuery returns floats; the reply prints them as integers.
    assert ev.numbers_match([995574.0], [995574], 0.0)
    assert ev.numbers_match([2328.6], [2328.6], 0.0)
    assert not ev.numbers_match([995574.0], [995575], 0.0)


def test_numbers_match_tolerance():
    assert ev.numbers_match([1_000_000], [1_000_400], 0.001)
    assert not ev.numbers_match([1_000_000], [1_010_000], 0.001)


def test_numbers_match_rejects_wrong_length():
    assert not ev.numbers_match([1, 2], [1], 0.0)


def test_extract_numbers_handles_thousands_and_symbols():
    text = "斯德哥尔摩约 995,574 人，增长 +0.67%，共 3 个市。"
    got = ev.extract_numbers(text)
    assert 995574.0 in got
    assert 0.67 in got


def test_summary_rates_are_per_kind():
    results = [
        ev.Result(case_id="a", kind="numbers", passed=True, detail=""),
        ev.Result(case_id="b", kind="numbers", passed=False, detail=""),
        ev.Result(case_id="c", kind="refusal", passed=True, detail=""),
        ev.Result(case_id="d", kind="refusal", passed=True, detail=""),
        ev.Result(case_id="e", kind="tool_used", passed=False, detail=""),
    ]
    s = ev.summarise(results)
    assert s["accuracy"] == 0.5
    assert s["guard_rate"] == 1.0
    assert s["tool_choice"] == 0.0
    assert s["cases"] == 5 and s["passed"] == 3


def test_missing_kind_does_not_divide_by_zero():
    s = ev.summarise([ev.Result(case_id="a", kind="numbers", passed=True, detail="")])
    assert s["guard_rate"] == 0.0 and s["tool_choice"] == 0.0


def test_dataset_file_parses_into_cases():
    import json
    from pathlib import Path

    path = Path(os.path.join(os.path.dirname(__file__), "..", "evals", "marts_core.json"))
    data = json.loads(path.read_text())
    cases = [ev.Case(**c) for c in data["cases"]]
    assert len(cases) >= 8
    kinds = {c.kind for c in cases}
    assert kinds == {"numbers", "refusal", "tool_used"}
    # Every refusal case needs a needle, or it would pass for the wrong reason.
    for c in cases:
        if c.kind == "refusal":
            assert c.expect_error_contains
        if c.kind == "tool_used":
            assert c.expect_tool
        if c.kind == "numbers":
            assert c.expect_sql and c.expect
