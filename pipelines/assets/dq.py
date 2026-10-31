"""
DQ ledger: every dbt test result becomes a row in meta.dq_results.

Parsing dbt's run_results.json is deliberate — it means the ledger can never
disagree with what dbt actually ran. Run it right after `dbt build`.
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
RUN_RESULTS = Path(__file__).resolve().parents[2] / "dbt" / "target" / "run_results.json"


@asset(group_name="meta")
def dq_results() -> None:
    """Append this run's test results to meta.dq_results."""
    if not RUN_RESULTS.exists():
        raise FileNotFoundError(f"{RUN_RESULTS} missing — run `dbt build` first")

    results = json.loads(RUN_RESULTS.read_text())["results"]
    run_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    rows = []
    for r in results:
        unique_id = r.get("unique_id", "")
        parts = unique_id.split(".")
        node = parts[-1]
        is_test = unique_id.startswith("test.")
        rows.append(
            {
                "run_at": run_at,
                "unique_id": unique_id,
                "node_type": parts[0] if parts else "",
                "table_name": node.split(".")[0] if is_test else node,
                "check_name": r.get("node_name") or node,
                "status": r.get("status", ""),
                "execution_time_s": r.get("execution_time"),
                "message": (r.get("message") or "")[:1000],
            }
        )

    df = pd.DataFrame(rows)
    client = bigquery.Client(project=PROJECT)
    cfg = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", autodetect=True)
    client.load_table_from_dataframe(df, f"{PROJECT}.{META}.dq_results", job_config=cfg).result()

    failed = int((df["status"] == "fail").sum())
    print(f"[dq_results] appended {len(df)} rows, {failed} failing")
