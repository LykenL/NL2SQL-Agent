"""DQ triage: ledger parsing, evidence queries, cause classification.

The triage path is read-only and answers three questions per failure: which test
broke, how bad is it (numbers from the gold table), and what to check next.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipelines.assets.dq import _describe  # noqa: E402
from src.core.dq_triage import DQTriage, PLAYBOOK, parse_test_args  # noqa: E402


class FakeJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class FakeClient:
    """Records SQL and replays canned rows, so tests never touch BigQuery."""

    def __init__(self, responses):
        self.responses = responses  # list of row-lists, consumed in order
        self.sql = []

    def query(self, sql, job_config=None):
        self.sql.append(sql)
        rows = self.responses.pop(0) if self.responses else []
        return FakeJob(rows)


# -- ledger row construction -------------------------------------------------
def test_ledger_row_uses_test_name_not_hash():
    """dbt test unique_ids end in a hash; the ledger must not."
    """
    nodes = {
        "test.data_copilot.not_null_mart_pop_municipality_code.abc123": {
            "name": "not_null_mart_pop_municipality_code",
            "test_metadata": {"name": "not_null", "kwargs": {"column_name": "municipality_code"}},
            "depends_on": {"nodes": ["model.data_copilot.mart_municipality_population"]},
            "config": {"severity": "error"},
        },
        "model.data_copilot.mart_municipality_population": {"name": "mart_municipality_population"},
    }
    row = _describe(
        {
            "unique_id": "test.data_copilot.not_null_mart_pop_municipality_code.abc123",
            "status": "fail",
            "failures": 7,
            "message": "got 7 results",
            "execution_time": 1.2,
        },
        "2026-10-31T00:00:00+00:00",
        nodes,
    )
    assert row["check_name"] == "not_null_mart_pop_municipality_code"
    assert row["table_name"] == "mart_municipality_population"
    assert row["test_type"] == "not_null"
    assert row["column_name"] == "municipality_code"
    assert row["severity"] == "error"
    assert row["failures"] == 7


def test_ledger_row_survives_missing_manifest_entry():
    row = _describe({"unique_id": "test.proj.some_singular_test.zzz", "status": "pass"}, "t", {})
    assert row["check_name"] == "some_singular_test"
    assert row["test_type"] == ""
    assert row["failures"] == 0


# -- triage ------------------------------------------------------------------
def _triage(client):
    return DQTriage(
        client=client,
        project="p",
        meta_dataset="p_gold".replace("_gold", "_meta"),
        gold_dataset="p_gold",
    )


def test_healthy_run_reports_no_items():
    client = FakeClient([[{"run_at": "2026-10-31T00:00:00+00:00"}], []])
    report = _triage(client).report()
    assert report["status"] == "healthy"
    assert report["items"] == []
    assert report["run_at"] == "2026-10-31T00:00:00+00:00"


def test_empty_ledger_is_unknown_not_healthy():
    client = FakeClient([[{"run_at": None}]])
    report = _triage(client).report()
    assert report["status"] == "unknown"
    assert "no runs" in report["reason"]


def test_not_null_failure_produces_numbers_and_cause():
    ledger_row = {
        "table_name": "mart_municipality_population",
        "check_name": "not_null_mart_municipality_population_population",
        "test_type": "not_null",
        "column_name": "population",
        "severity": "error",
        "failures": 900,
        "message": "got 900 results",
        "run_at": "2026-10-31T00:00:00+00:00",
        "test_args": "{}",
    }
    client = FakeClient(
        [
            [{"run_at": "2026-10-31T00:00:00+00:00"}],  # latest_run_at
            [ledger_row],                                # failures
            [{"rows_total": 1000, "rows_bad": 900}],     # evidence
        ]
    )
    report = _triage(client).report()

    assert report["status"] == "failing"
    assert report["gold_published"] is False
    item = report["items"][0]
    assert item["evidence"]["rows_bad"] == 900
    assert item["evidence"]["rows_bad_share"] == 0.9
    assert "90%" in item["likely_cause"]  # mostly-empty column hint
    assert "staging model" in item["suggested_fix"]
    assert item["table"] == "mart_municipality_population"


def test_unique_evidence_query_groups_by_column():
    f_row = {
        "table_name": "mart_kpi_summary",
        "check_name": "unique_x",
        "test_type": "unique",
        "column_name": "kpi_id",
        "severity": "warn",
        "failures": 2,
        "message": "",
        "run_at": "t",
        "test_args": "{}",
    }
    client = FakeClient([[{"run_at": "t"}], [f_row], [{"rows_bad": 3}]])
    report = _triage(client).report()
    evidence_sql = report["items"][0]["evidence"]["sql"]
    assert "GROUP BY `kpi_id`" in evidence_sql
    assert "HAVING COUNT(*) > 1" in evidence_sql
    assert report["severity"] == "warning"  # warn severity, not error


def test_relationships_evidence_uses_args():
    f_row = {
        "table_name": "mart_personnel_cost",
        "check_name": "relationships_y",
        "test_type": "relationships",
        "column_name": "municipality_code",
        "severity": "error",
        "failures": 1,
        "message": "",
        "run_at": "t",
        "test_args": '{"to": "ref(\'mart_municipality_population\')", "field": "municipality_code"}',
    }
    client = FakeClient([[{"run_at": "t"}], [f_row], [{"rows_bad": 12}]])
    report = _triage(client).report()
    sql = report["items"][0]["evidence"]["sql"]
    assert "NOT EXISTS" in sql
    assert "mart_municipality_population" in sql
    assert report["items"][0]["evidence"]["rows_bad"] == 12


def test_triage_refuses_non_select():
    client = FakeClient([])
    triage = _triage(client)
    try:
        triage._query("DELETE FROM x")
    except PermissionError as exc:
        assert "SELECT" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("triage executed a non-SELECT statement")


def test_every_ledger_test_type_has_a_playbook_entry():
    for name in ("not_null", "unique", "relationships", "accepted_values", "expression"):
        assert name in PLAYBOOK
        cause, step = PLAYBOOK[name]
        assert cause and step


def test_parse_test_args_tolerates_garbage():
    assert parse_test_args(None) == {}
    assert parse_test_args("not json") == {}
    assert parse_test_args('["a"]') == {}
    assert parse_test_args('{"column_name": "x"}') == {"column_name": "x"}
