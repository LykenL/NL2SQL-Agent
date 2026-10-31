#!/usr/bin/env python3
"""
One command that refreshes the whole warehouse. This is what the Render Cron Job
runs:

    python scripts/refresh.py

Steps:
  1. ingest  — Kolada API → bronze (Dagster assets)
  2. build   — dbt: silver views + gold marts + tests
  3. ledger  — dbt run_results → meta.dq_results
  4. gate    — fail the process if any test failed (blocks downstream consumers)

Exit code 0 only when every step succeeded and the DQ gate passed, so a scheduler
can alert on failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "pipelines" / "definitions.py"
BRONZE_SELECT = "kolada_municipalities,kolada_kpi_catalog,kolada_kpi_values"


def run(title: str, cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("$ " + " ".join(cmd), flush=True)
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env or os.environ.copy())
    if proc.returncode != 0:
        print(f"FAILED ({title}) after {time.time() - started:.1f}s", flush=True)
        sys.exit(proc.returncode)
    print(f"ok ({time.time() - started:.1f}s)", flush=True)


def main() -> int:
    # Dagster needs a writable instance dir; Render's filesystem is ephemeral.
    os.environ.setdefault("DAGSTER_HOME", "/tmp/dagster_home")
    os.makedirs(os.environ["DAGSTER_HOME"], exist_ok=True)

    dbt_env = os.environ.copy()
    dbt_env.setdefault("DBT_PROFILES_DIR", str(ROOT / "dbt"))

    run("1/4 ingest (Kolada → bronze)",
        ["dagster", "asset", "materialize", "-f", str(DEFS), "--select", BRONZE_SELECT])

    run("2/4 dbt build (silver + gold + tests)",
        ["dbt", "build", "--fail-fast"], cwd=ROOT / "dbt", env=dbt_env)

    run("3/4 dq ledger",
        ["dagster", "asset", "materialize", "-f", str(DEFS), "--select", "dq_results"])

    run("4/4 dq gate",
        ["dagster", "asset", "materialize", "-f", str(DEFS), "--select", "dq_gate"])

    print("\nrefresh complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
