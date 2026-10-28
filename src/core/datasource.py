"""
Data sources the agent can query.

Two shapes:
- SqlDataSource : anything SQLAlchemy can reach (sqlite / postgres / mysql).
- BigQueryDataSource: project + dataset, authenticated server-side via a service
  account (Render Secret File -> GOOGLE_APPLICATION_CREDENTIALS).

BigQuery is deliberately NOT exposed as a connection string: credentials never
leave the server, every statement is dry-run for bytes/cost before it executes,
and results are capped. The agent reaches it through tools, not through an
ambient DATABASE_URI.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# On-demand BigQuery pricing, USD per TiB scanned.
DEFAULT_PRICE_PER_TIB = 5.0


@dataclass
class Estimate:
    bytes_processed: int
    usd: float
    budget_bytes: Optional[int] = None

    @property
    def over_budget(self) -> bool:
        return bool(self.budget_bytes) and self.bytes_processed > self.budget_bytes

    def human(self) -> str:
        gb = self.bytes_processed / 1e9
        unit = f"{gb:,.2f} GB" if gb < 1000 else f"{gb / 1000:,.2f} TB"
        return f"{unit} (~${self.usd:,.4f} @ ${DEFAULT_PRICE_PER_TIB:g}/TiB)"


class DataSource:
    dialect: str = "unknown"
    label: str = "unknown"
    supports_estimates: bool = False

    # -- schema -------------------------------------------------------------
    def schema_json(self) -> str:
        raise NotImplementedError

    def describe(self, table: str) -> str:
        """Cheap, no-query description of one table (from the cached schema)."""
        schema = json.loads(self.schema_json())
        match = next((t for t in schema if t.lower() == table.lower()), None)
        if match is None:
            return f"Table '{table}' not found. Available: {', '.join(list(schema)[:20])}"
        return json.dumps({match: schema[match]}, indent=2)

    # -- execution ----------------------------------------------------------
    def estimate(self, sql: str) -> Optional[Estimate]:
        return None

    def run(self, sql: str, max_rows: int = 1000) -> pd.DataFrame:
        raise NotImplementedError

    # -- prompt -------------------------------------------------------------
    def dialect_notes(self) -> str:
        return ""


# --------------------------------------------------------------------------- #
# SQLAlchemy-backed sources
# --------------------------------------------------------------------------- #

class SqlDataSource(DataSource):
    def __init__(self, uri: str):
        self.uri = uri
        self.dialect = uri.split("://", 1)[0].split("+", 1)[0] or "sql"
        self.label = uri.split("/")[-1] or self.dialect
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine

            connect_args = {"connect_timeout": 10} if self.dialect in ("postgres", "postgresql", "mysql") else {}
            self._engine = create_engine(self.uri, connect_args=connect_args)
        return self._engine

    def schema_json(self) -> str:
        from src.core.db_reader import extract_db_schema

        return extract_db_schema(self.uri)

    def run(self, sql: str, max_rows: int = 1000) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(sql, conn).head(max_rows)

    def dialect_notes(self) -> str:
        if self.dialect in ("postgres", "postgresql"):
            return "Dialect: PostgreSQL. Use DATE_TRUNC('month', ts), ILIKE, and standard quoting."
        if self.dialect == "mysql":
            return "Dialect: MySQL. Use DATE_FORMAT(ts, '%Y-%m'), LIMIT n."
        return "Dialect: SQLite. Use strftime('%Y-%m', date_col) for months."


# --------------------------------------------------------------------------- #
# BigQuery
# --------------------------------------------------------------------------- #

_BQ_READONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_BQ_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|drop|alter|truncate|export|load)\b", re.IGNORECASE
)


class BigQueryDataSource(DataSource):
    dialect = "bigquery"
    supports_estimates = True

    def __init__(
        self,
        project: str,
        dataset: str,
        credentials_path: Optional[str] = None,
        location: Optional[str] = None,
        max_bytes: Optional[int] = None,
        max_rows: int = 1000,
        billing_project: Optional[str] = None,
    ):
        # `project` owns the data; `billing_project` pays for the jobs (they differ
        # for public datasets, e.g. bigquery-public-data.* billed to your project).
        self.project = project
        self.dataset = dataset
        self.billing_project = billing_project or os.getenv("BIGQUERY_BILLING_PROJECT") or project
        self.location = location or os.getenv("BIGQUERY_LOCATION") or "US"
        self.credentials_path = credentials_path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.max_bytes = max_bytes if max_bytes is not None else _env_int("BQ_MAX_BYTES")
        self.max_rows = max_rows
        self.label = f"{project}.{dataset}"
        self._client = None
        self._schema: Optional[str] = None

    # -- client -------------------------------------------------------------
    def _make_client(self, client: Any = None):
        if client is not None:
            return client
        from google.cloud import bigquery
        from google.oauth2 import service_account

        if self.credentials_path and Path(self.credentials_path).exists():
            creds = service_account.Credentials.from_service_account_file(self.credentials_path)
            return bigquery.Client(project=self.billing_project, credentials=creds, location=self.location)
        # fall back to ADC (local development)
        return bigquery.Client(project=self.billing_project, location=self.location)

    @property
    def client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    # -- schema -------------------------------------------------------------
    def schema_json(self) -> str:
        """INFORMATION_SCHEMA scan: column types, descriptions, partition/cluster keys.

        BigQuery has no foreign keys, so the schema carries what the agent needs
        instead: table descriptions, partitioning and clustering columns.
        """
        if self._schema is not None:
            return self._schema

        # Metadata API (get_dataset/get_table) instead of INFORMATION_SCHEMA:
        # it scans zero bytes, needs no billing on the data project, and works
        # for public datasets where the owner project is not yours.
        dataset = self.client.get_dataset(f"{self.project}.{self.dataset}")
        schema: dict[str, dict] = {}

        for table_ref in self.client.list_tables(dataset):
            table = self.client.get_table(table_ref)
            entry: dict = {
                "columns": [],
                "foreign_keys": [],
                "note": "BigQuery has no FK constraints",
            }
            if getattr(table, "description", None):
                entry["description"] = table.description
            part_field = getattr(getattr(table, "time_partitioning", None), "field", None)
            if part_field:
                entry["partition_column"] = part_field
            if getattr(table, "clustering_fields", None):
                entry["clustering_columns"] = list(table.clustering_fields)
            for f in table.schema:
                col: dict = {"name": f.name, "type": f.field_type}
                if getattr(f, "description", None):
                    col["description"] = f.description
                if part_field and f.name == part_field:
                    col["partition_key"] = True
                if f.name in (getattr(table, "clustering_fields", None) or []):
                    col["clustering_position"] = list(table.clustering_fields).index(f.name) + 1
                entry["columns"].append(col)
            schema[table_ref.table_id] = entry

        self._schema = json.dumps(schema, indent=2)
        return self._schema

    # -- execution ----------------------------------------------------------
    def estimate(self, sql: str) -> Estimate:
        from google.cloud import bigquery

        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = self.client.query(sql, job_config=cfg)
        nbytes = int(getattr(job, "total_bytes_processed", 0) or 0)
        usd = nbytes / (1024 ** 4) * DEFAULT_PRICE_PER_TIB
        return Estimate(bytes_processed=nbytes, usd=usd, budget_bytes=self.max_bytes)

    def _assert_readonly(self, sql: str) -> None:
        if not _BQ_READONLY.match(sql):
            raise PermissionError("Only SELECT / WITH statements are allowed against BigQuery.")
        # Strip string literals before keyword screening so a column named
        # "delete_flag" does not trip the guard.
        stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", sql)
        if _BQ_FORBIDDEN.search(stripped):
            raise PermissionError("Statement contains a write/DDL keyword; blocked before execution.")

    def run(self, sql: str, max_rows: Optional[int] = None) -> pd.DataFrame:
        self._assert_readonly(sql)
        limit = max_rows or self.max_rows
        wrapped = f"SELECT * FROM (\n{sql}\n) LIMIT {int(limit)}"
        job = self.client.query(wrapped)
        # Build the frame from rows directly: to_dataframe() drags in db-dtypes
        # for DATE/TIME columns, which is an extra runtime dependency we do not need.
        df = pd.DataFrame([dict(row.items()) for row in job.result()])
        billed = int(getattr(job, "total_bytes_processed", 0) or 0)
        df.attrs["bytes_processed"] = billed
        df.attrs["bytes_billed_usd"] = round(billed / (1024 ** 4) * DEFAULT_PRICE_PER_TIB, 4)
        return df

    def dialect_notes(self) -> str:
        return (
            "Dialect: BigQuery (GoogleSQL). Quote tables as `project.dataset.table`. "
            "Use DATE_TRUNC(ts, MONTH) or FORMAT_DATE('%Y-%m', DATE(ts)) — NOT strftime. "
            "No foreign keys exist: join on naming conventions and the column descriptions. "
            "Run estimate_query first for any query that might scan a large table."
        )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def _env_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def source_from_spec(spec: Optional[str], uri: Optional[str] = None) -> DataSource:
    """
    Build a DataSource from a UI selector.

    spec forms:
      None / ""                      -> SqlDataSource(uri)
      "bigquery:project.dataset"     -> BigQueryDataSource
    """
    if spec and spec.startswith("bigquery:"):
        target = spec.split(":", 1)[1]
        project, _, dataset = target.partition(".")
        if not project or not dataset:
            raise ValueError(f"invalid bigquery spec: {spec}")
        return BigQueryDataSource(project=project, dataset=dataset)
    if not uri:
        raise ValueError("no data source selected")
    return SqlDataSource(uri)


def configured_bigquery_targets() -> list[dict]:
    """Datasets exposed to the UI, from BIGQUERY_DATASETS="project.dataset,project.dataset2"."""
    raw = os.getenv("BIGQUERY_DATASETS", "").strip()
    out = []
    for item in filter(None, (x.strip() for x in raw.split(","))):
        project, _, dataset = item.partition(".")
        if project and dataset:
            out.append({"id": f"bigquery:{project}.{dataset}", "label": item, "dialect": "bigquery"})
    return out
