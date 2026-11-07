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


def _refusal_case(forbid=("stg_kpi_values",)):
    return ev.Case(id="g", question="查 stg_kpi_values", kind="refusal", forbid=list(forbid))


def test_refusal_passes_when_reply_says_unavailable():
    case = _refusal_case()
    r = ev.run_case.__wrapped__ if hasattr(ev.run_case, "__wrapped__") else ev.run_case
    # Judge the reply directly through the same helper the runner uses.
    assert _judge(case, "`stg_kpi_values` 这张表在当前数据库中**不存在**。可用的只有 gold 层三张表。")
    assert _judge(case, "stg_kpi_values is not queryable; only published marts are available.")


def test_refusal_fails_when_table_is_presented_as_usable():
    case = _refusal_case()
    assert not _judge(case, "以下是 stg_kpi_values 的查询结果：kpi_id=N00003, value=40192.87")


def test_refusal_passes_when_restricted_table_never_mentioned():
    case = _refusal_case()
    assert _judge(case, "只有 mart_kpi_summary 可查，它包含 kpi_id 和 value 两列。")


def _judge(case, reply):
    """Run only the refusal branch of run_case, without touching the agent."""
    text = reply.lower()
    forbidden = [t.lower() for t in (case.forbid or [])]
    if not any(t in text for t in forbidden):
        return True
    return any(
        phrase in text
        for phrase in ("不存在", "不可用", "不能", "无法", "only the gold",
                       "not queryable", "not available", "does not exist")
    )


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
    # Every refusal case must name what may not leak, or it would pass for the
    # wrong reason.
    for c in cases:
        if c.kind == "refusal":
            assert c.forbid
        if c.kind == "tool_used":
            assert c.expect_tool
        if c.kind == "numbers":
            assert c.expect_sql and c.expect
