"""DQ gate: a blocking asset, not an advisory check.

Downstream publishing (copilot data health) selects `dq_gate+`, so a failing
dbt test stops the pipeline instead of leaving a warning in a log.
"""

import os

from dagster import asset, AssetExecutionContext

PROJECT = os.getenv("BQ_PROJECT", "woven-surface-511317-g0")


@asset(group_name="meta", deps=["dq_results"])
def dq_gate(context: AssetExecutionContext) -> None:
    from google.cloud import bigquery
    from dagster import Failure

    meta = f"{os.getenv('BQ_PREFIX', 'data_copilot_dev')}_meta"
    q = f"""
    select count(*) as failures
    from `{PROJECT}.{meta}.dq_results`
    where status = 'fail'
      and run_at = (select max(run_at) from `{PROJECT}.{meta}.dq_results`)
    """
    failures = list(bigquery.Client(project=PROJECT).query(q))[0]["failures"]
    context.log.info(f"failing dbt tests in the latest run: {failures}")
    if failures:
        raise Failure(description=f"{failures} dbt test(s) failed — gold refresh blocked")
