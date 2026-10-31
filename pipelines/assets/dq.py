"""
DQ ledger: every dbt test result becomes a row in meta.dq_results.

Parsing dbt's run_results.json is deliberate — it means the ledger can never
disagree with what dbt actually ran. Run it right after `dbt build`.

Rows are enriched from the dbt manifest, because a generic test's `unique_id`
ends in a hash (`test.project.not_null_tbl_col.3eb6219b10`). Storing the raw
unique_id made the ledger unable to answer "which test failed" — the earlier
version wrote the hash into both `check_name` and `table_name`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd
from dagster import asset
from google.cloud import bigquery

PROJECT = os.getenv("BQ_PROJECT", "woven-surface-511317-g0")
PREFIX = os.getenv("BQ_PREFIX", "data_copilot_dev")
META = f"{PREFIX}_meta"
DBT_TARGET = Path(__file__).resolve().parents[2] / "dbt" / "target"
RUN_RESULTS = DBT_TARGET / "run_results.json"
MANIFEST = DBT_TARGET / "manifest.json"


def _manifest_nodes() -> dict:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text()).get("nodes", {}) or {}


def _describe(result: dict, run_at: str, nodes: dict) -> dict:
    """One ledger row: real test name, test type, column and the model it guards."""
    uid = result.get("unique_id") or ""
    parts = uid.split(".")
    node = nodes.get(uid) or {}
    node_type = parts[0] if parts else ""

    # parts[-1] is a hash for generic tests; parts[-2] is the human name.
    fallback_name = parts[-2] if len(parts) >= 3 else uid
    name = node.get("name") or fallback_name

    test_meta = node.get("test_metadata") or {}
    kwargs = test_meta.get("kwargs") or {}

    model = ""
    for dep in (node.get("depends_on") or {}).get("nodes", []) or []:
        if dep.startswith("model."):
            model = (nodes.get(dep) or {}).get("name") or dep.split(".")[-1]
            break

    return {
        "run_at": run_at,
        "unique_id": uid,
        "node_type": node_type,
        "table_name": model or name,
        "check_name": name,
        "test_type": test_meta.get("name") or "",
        "column_name": str(kwargs.get("column_name") or kwargs.get("column") or ""),
        "test_args": json.dumps(kwargs)[:1000],
        "severity": str((node.get("config") or {}).get("severity") or "").lower(),
        "status": result.get("status", ""),
        "failures": int(result.get("failures") or 0),
        "execution_time_s": result.get("execution_time"),
        "message": (result.get("message") or "")[:1000],
    }


@asset(group_name="meta")
def dq_results() -> None:
    """Append this run's test results to meta.dq_results."""
    if not RUN_RESULTS.exists():
        raise FileNotFoundError(f"{RUN_RESULTS} missing — run `dbt build` first")

    results = json.loads(RUN_RESULTS.read_text())["results"]
    nodes = _manifest_nodes()
    run_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    df = pd.DataFrame([_describe(r, run_at, nodes) for r in results])

    client = bigquery.Client(project=PROJECT)
    cfg = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        autodetect=True,
        # The ledger predates test_type / column_name / severity / failures; adding
        # columns needs an explicit schema-update option on the load job.
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
    )
    client.load_table_from_dataframe(df, f"{PROJECT}.{META}.dq_results", job_config=cfg).result()

    failed = int((df["status"] == "fail").sum())
    print(f"[dq_results] appended {len(df)} rows, {failed} failing")
