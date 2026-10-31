"""
Read-only data-quality triage.

`dq_gate` already blocks publication when a dbt test fails. Blocking is not the
same as explaining: an on-call engineer still has to open the ledger, find the
failing test, query the table and work out the cause. This module does that
first pass mechanically, and returns numbers rather than adjectives.

What it is:
  - reads the latest run from `meta.dq_results`
  - for each failing test, runs ONE cheap evidence query against the guarded
    gold table (null count, duplicate keys, orphan count, out-of-range values)
  - attaches a heuristic cause and a concrete next step

What it is not:
  - it never writes, never fixes anything and never queries silver/bronze
  - its cause labels are heuristics; the evidence query is the ground truth

Consumed by two callers: the `diagnose_data_quality` agent tool, and
`GET /api/dq/triage`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

# test_type -> (likely cause, suggested next step)
PLAYBOOK: dict[str, tuple[str, str]] = {
    "not_null": (
        "The column is arriving empty. Most often an upstream rename, a broken "
        "join, or a source that started publishing blanks.",
        "Check the staging model for that column: was it renamed, or is the join "
        "to the source table dropping rows? Compare null counts before and after "
        "the join.",
    ),
    "unique": (
        "Duplicate keys, usually a join fan-out — one row joined to many — or the "
        "grain changed upstream.",
        "Group by the key and inspect the duplicated rows: if every duplicate "
        "differs only in a dimension column, restrict the join to one row per key.",
    ),
    "relationships": (
        "Orphan foreign keys: values in the child column have no match in the "
        "parent table.",
        "Check whether the parent table is filtered or truncated relative to the "
        "child, and whether the key needs normalising (case, whitespace, type).",
    ),
    "accepted_values": (
        "Values outside the allowed set — a new category, a casing change, or a "
        "localised label.",
        "List the distinct offending values; if they are legitimate, extend the "
        "accepted list in the model's schema.yml instead of loosening the test.",
    ),
    "expression": (
        "A custom expression failed; the test message describes the violated rule.",
        "Use the evidence count to size the problem, then read the test SQL in the "
        "dbt manifest for the exact predicate.",
    ),
}

GENERIC_PLAYBOOK = (
    "A dbt test failed. The message field carries the rule that was violated.",
    "Reproduce with the evidence query, then decide whether the data or the test "
    "is wrong before changing either.",
)


@dataclass
class Failure:
    table: str
    test_name: str
    test_type: str
    column: str
    severity: str
    failures: int
    message: str
    run_at: str
    test_args: dict[str, Any] = field(default_factory=dict)


def parse_test_args(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


class DQTriage:
    """Read-only diagnosis over one DQ ledger."""

    def __init__(
        self,
        client,
        project: str,
        meta_dataset: str,
        gold_dataset: str,
        max_bytes_billed: int = 2_000_000_000,
    ) -> None:
        self.client = client
        self.project = project
        self.meta_dataset = meta_dataset
        self.gold_dataset = gold_dataset
        self.max_bytes_billed = max_bytes_billed

    # -- plumbing -----------------------------------------------------------
    def _query(self, sql: str, params: Optional[list] = None) -> list[dict]:
        if not SELECT_ONLY.match(sql):
            raise PermissionError("triage runs SELECT/WITH statements only")
        job_config = None
        try:
            from google.cloud import bigquery

            job_config = bigquery.QueryJobConfig(
                maximum_bytes_billed=self.max_bytes_billed,
                query_parameters=params or [],
            )
        except Exception:  # pragma: no cover - only when the SDK is absent
            job_config = None
        job = self.client.query(sql, job_config=job_config) if job_config else self.client.query(sql)
        return [dict(row.items()) if hasattr(row, "items") else dict(row) for row in job.result()]

    def _table(self, name: str) -> str:
        return f"`{self.project}.{self.gold_dataset}.{name}`"

    def latest_run_at(self) -> Optional[str]:
        """Latest run, for display only.

        The ledger column is a STRING in datasets created before dq.py wrote
        typed rows and a TIMESTAMP after, so nothing here compares it across
        types: filtering always happens in SQL against MAX(run_at).
        """
        rows = self._query(
            f"SELECT CAST(MAX(run_at) AS STRING) AS run_at "
            f"FROM `{self.project}.{self.meta_dataset}.dq_results`"
        )
        return rows[0]["run_at"] if rows else None

    def failures(self) -> list[Failure]:
        """Failures from the newest run, selected entirely in SQL."""
        ledger = f"`{self.project}.{self.meta_dataset}.dq_results`"
        rows = self._query(
            f"""
            SELECT table_name, check_name, test_type, column_name, severity,
                   failures, message, CAST(run_at AS STRING) AS run_at, test_args
            FROM {ledger}
            WHERE status = 'fail'
              AND run_at = (SELECT MAX(run_at) FROM {ledger})
            ORDER BY failures DESC
            """
        )
        return [
            Failure(
                table=r.get("table_name") or "",
                test_name=r.get("check_name") or "",
                test_type=r.get("test_type") or "",
                column=r.get("column_name") or "",
                severity=r.get("severity") or "",
                failures=int(r.get("failures") or 0),
                message=(r.get("message") or "")[:500],
                run_at=r.get("run_at") or "",
                test_args=parse_test_args(r.get("test_args")),
            )
            for r in rows
        ]

    # -- evidence -----------------------------------------------------------
    def evidence(self, f: Failure) -> dict:
        """One cheap query that turns the test failure into numbers."""
        if not f.table or not f.column and f.test_type in {"not_null", "unique", "accepted_values"}:
            return {"error": "ledger row lacks table or column", "sql": ""}

        col = f.column.replace("`", "")
        table = self._table(f.table)

        if f.test_type == "not_null":
            sql = (
                f"SELECT COUNT(*) AS rows_total, COUNTIF(`{col}` IS NULL) AS rows_bad "
                f"FROM {table}"
            )
        elif f.test_type == "unique":
            sql = (
                f"SELECT COUNT(*) AS rows_bad FROM ("
                f"SELECT `{col}` FROM {table} GROUP BY `{col}` HAVING COUNT(*) > 1)"
            )
        elif f.test_type == "relationships":
            to_table = str(f.test_args.get("to") or "").split(".")[-1]
            to_field = str(f.test_args.get("field") or "")
            if not to_table or not to_field:
                return {"error": "relationships test args missing to/field", "sql": ""}
            sql = (
                f"SELECT COUNT(*) AS rows_bad FROM {table} AS c "
                f"WHERE c.`{col}` IS NOT NULL AND NOT EXISTS ("
                f"SELECT 1 FROM {self._table(to_table)} AS p WHERE p.`{to_field}` = c.`{col}`)"
            )
        elif f.test_type == "accepted_values":
            values = f.test_args.get("values") or []
            if not values:
                return {"error": "accepted_values test args missing values", "sql": ""}
            quoted = ", ".join("'" + str(v).replace("'", "\\'") + "'" for v in values)
            sql = (
                f"SELECT COUNT(*) AS rows_bad FROM {table} "
                f"WHERE `{col}` IS NOT NULL AND `{col}` NOT IN ({quoted})"
            )
        else:
            return {"error": f"no evidence query for test type '{f.test_type}'", "sql": ""}

        try:
            rows = self._query(sql)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "sql": sql}

        out = dict(rows[0]) if rows else {}
        total = out.get("rows_total")
        bad = out.get("rows_bad", 0)
        if total:
            out["rows_bad_share"] = round(bad / total, 4) if total else None
        out["sql"] = sql
        return out

    # -- report -------------------------------------------------------------
    def report(self) -> dict:
        run_at = self.latest_run_at()
        if not run_at:
            return {
                "status": "unknown",
                "reason": "the DQ ledger has no runs yet — run the pipeline first",
                "failing": 0,
                "items": [],
            }

        failures = self.failures()
        if not failures:
            return {
                "status": "healthy",
                "run_at": run_at,
                "failing": 0,
                "message": "the latest dbt run had no failing tests",
                "items": [],
            }

        items = []
        for f in failures:
            cause, step = PLAYBOOK.get(f.test_type, GENERIC_PLAYBOOK)
            ev = self.evidence(f)
            items.append(
                {
                    **asdict(f),
                    "evidence": ev,
                    "likely_cause": self._refine_cause(f, ev, cause),
                    "suggested_fix": step,
                }
            )

        worst = (
            "critical"
            if any(str(i.get("severity") or "").lower() in {"error", "critical"} for i in items)
            else "warning"
        )
        return {
            "status": "failing",
            "run_at": run_at,
            "failing": len(items),
            "severity": worst,
            "gold_published": False,
            "items": items,
        }

    @staticmethod
    def _refine_cause(f: Failure, ev: dict, default: str) -> str:
        """Sharpen the playbook text with what the evidence query actually found."""
        bad = ev.get("rows_bad")
        total = ev.get("rows_total")
        if bad in (None, 0):
            return default
        if f.test_type == "not_null" and total:
            share = bad / total
            if share > 0.5:
                return (
                    f"{bad:,} of {total:,} rows are null ({share:.0%}). A column that "
                    "is mostly empty points at a rename or a join that no longer "
                    "matches, not at a few bad records."
                )
            return f"{bad:,} of {total:,} rows are null ({share:.1%}) — a partial gap."
        if f.test_type == "unique":
            return f"{bad:,} duplicated key value(s); the grain of the table changed or a join fanned out."
        if f.test_type == "relationships":
            return f"{bad:,} orphan key value(s) with no parent row."
        if f.test_type == "accepted_values":
            return f"{bad:,} row(s) hold values outside the accepted set."
        return default


def triage_from_env(max_bytes_billed: int = 2_000_000_000) -> dict:
    """Convenience wrapper for the API: uses the same env as MartSource."""
    import os

    from google.cloud import bigquery

    project = os.getenv("BQ_PROJECT", "woven-surface-511317-g0")
    dataset = os.getenv("BQ_DATASET", f"{os.getenv('BQ_PREFIX', 'data_copilot_dev')}_gold")
    triage = DQTriage(
        client=bigquery.Client(project=project),
        project=project,
        meta_dataset=dataset.replace("_gold", "_meta"),
        gold_dataset=dataset,
        max_bytes_billed=max_bytes_billed,
    )
    return triage.report()
