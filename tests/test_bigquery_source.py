"""BigQuery data source: schema, dry-run estimate, read-only guard, row cap.

No network: a fake client stands in for google.cloud.bigquery.
"""

import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.agent_tools import create_agent_tools  # noqa: E402
from src.core.datasource import BigQueryDataSource, SqlDataSource, source_from_spec  # noqa: E402

class FField:
    def __init__(self, name, field_type, description=None):
        self.name, self.field_type, self.description = name, field_type, description


class FPartitioning:
    def __init__(self, field):
        self.field = field


class FTable:
    def __init__(self):
        self.table_id = "orders"
        self.description = "one row per order"
        self.time_partitioning = FPartitioning("order_date")
        self.clustering_fields = ["amount"]
        self.schema = [
            FField("order_id", "INT64", "primary key"),
            FField("order_date", "DATE", "order date"),
            FField("amount", "NUMERIC", "gross amount"),
        ]


class FTableRef:
    table_id = "orders"


class FDataset:
    dataset_id = "ds"

DATA_ROWS = [{"month": "2026-01", "revenue": 12.5}, {"month": "2026-02", "revenue": 30.0}]


class FakeJob:
    def __init__(self, rows, nbytes=0):
        self._rows = rows
        self.total_bytes_processed = nbytes

    def result(self):
        return self

    def __iter__(self):
        return iter(self._rows)

    def to_dataframe(self):
        return pd.DataFrame(self._rows)


class FakeClient:
    def __init__(self, nbytes=12 * 10 ** 9):
        self.nbytes = nbytes
        self.calls = []

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        if job_config is not None and getattr(job_config, "dry_run", False):
            return FakeJob([], nbytes=self.nbytes)
        return FakeJob(DATA_ROWS, nbytes=self.nbytes)

    # metadata API (no query, no cost)
    def get_dataset(self, ref):
        self.calls.append((f"get_dataset:{ref}", None))
        return FDataset()

    def list_tables(self, dataset):
        self.calls.append((f"list_tables:{dataset.dataset_id}", None))
        return [FTableRef()]

    def get_table(self, ref):
        self.calls.append((f"get_table:{ref.table_id}", None))
        return FTable()


@pytest.fixture()
def bq():
    ds = BigQueryDataSource(project="proj", dataset="ds", max_bytes=5 * 10 ** 9)
    ds._client = FakeClient()
    return ds


def test_schema_uses_metadata_api(bq):
    import json

    schema = json.loads(bq.schema_json())
    assert set(schema) == {"orders"}
    entry = schema["orders"]
    cols = {c["name"]: c for c in entry["columns"]}
    assert cols["order_date"]["partition_key"] is True
    assert cols["amount"]["clustering_position"] == 1
    assert cols["amount"]["description"] == "gross amount"
    assert entry["description"] == "one row per order"
    assert entry["partition_column"] == "order_date"
    # BigQuery has no FKs; the limitation is stated instead of silently empty
    assert "no FK" in entry["note"]
    # and no SQL was issued for the schema
    assert all(not call[0].upper().startswith("SELECT") for call in bq._client.calls)


def test_schema_is_cached(bq):
    bq.schema_json()
    calls = len(bq._client.calls)
    bq.schema_json()
    assert len(bq._client.calls) == calls


def test_estimate_reports_bytes_and_cost(bq):
    est = bq.estimate("SELECT 1")
    assert est.bytes_processed == 12 * 10 ** 9
    # 12 GB at $5/TiB
    assert 0.05 < est.usd < 0.06
    assert est.over_budget is True  # budget is 5 GB
    assert "GB" in est.human()


def test_run_wraps_limit_and_records_billing(bq):
    df = bq.run("SELECT month, revenue FROM orders", max_rows=500)
    sql, cfg = bq._client.calls[-1]
    assert "LIMIT 500" in sql and "SELECT * FROM (" in sql
    assert len(df) == 2
    assert df.attrs["bytes_processed"] == 12 * 10 ** 9


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders WHERE 1=1",
    "update orders set amount = 0",
    "CREATE TABLE x AS SELECT 1",
    "SELECT 1; DROP TABLE orders",
])
def test_write_statements_are_blocked(bq, sql):
    with pytest.raises(PermissionError):
        bq.run(sql)


def test_column_named_like_a_keyword_is_allowed(bq):
    df = bq.run("SELECT deleted_flag, created_at FROM orders")
    assert not df.empty


def test_source_from_spec_parses_project_dataset():
    ds = source_from_spec("bigquery:my-proj.analytics")
    assert isinstance(ds, BigQueryDataSource)
    assert ds.project == "my-proj" and ds.dataset == "analytics"


def test_tools_include_estimate_and_no_uri(bq):
    tools, tool_map = create_agent_tools(source=bq)
    names = [t["function"]["name"] for t in tools]
    assert names == ["get_database_schema", "describe_table", "estimate_query", "run_query", "execute_python_code"]

    out = tool_map["estimate_query"]("SELECT * FROM orders")
    assert "Estimated scan" in out and "OVER BUDGET" in out

    # arbitrary client construction is refused
    blocked = tool_map["execute_python_code"]("from google.cloud import bigquery")
    assert "Blocked" in blocked

    # DATABASE_URI is not handed out on BigQuery sessions
    res = tool_map["execute_python_code"]("print('DATABASE_URI' in globals())")
    assert "False" in res


def test_sql_source_still_gets_uri_and_no_estimate(tmp_path):
    db = tmp_path / "t.db"
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("create table t (a int)")
    conn.commit()
    conn.close()

    ds = SqlDataSource(f"sqlite:///{db}")
    tools, tool_map = create_agent_tools(source=ds)
    names = [t["function"]["name"] for t in tools]
    assert "estimate_query" not in names
    assert "True" in tool_map["execute_python_code"]("print('DATABASE_URI' in globals())")
