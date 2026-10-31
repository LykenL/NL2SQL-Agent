"""
Gold-only data source for the copilot.

The agent can only see published marts. silver / bronze references are rejected
before the query reaches BigQuery, so "the agent improvised on raw data" is not a
prompt-level promise — it is a code path that does not exist.

Mart list and freshness come from the dbt manifest and `meta.mart_freshness`,
which are produced by the pipeline, not hand-maintained.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from src.core.datasource import BigQueryDataSource

MANIFEST_PATH = Path(
    os.getenv(
        "DBT_MANIFEST",
        str(Path(__file__).resolve().parents[2] / "dbt" / "target" / "manifest.json"),
    )
)

# `project.dataset.table`, with optional backticks (BigQuery style).
_TABLE_REF = re.compile(r"`?([\w-]+)\.([\w-]+)\.([\w-]+)`?")


class MartSource(BigQueryDataSource):
    def __init__(
        self,
        project: str,
        dataset: str,
        allowed: Optional[set[str]] = None,
        meta_dataset: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(project=project, dataset=dataset, **kwargs)
        self.allowed = allowed if allowed is not None else self._allowed_from_manifest()
        self.meta_dataset = meta_dataset or dataset.replace("_gold", "_meta")

    # -- published tables ---------------------------------------------------
    @staticmethod
    def _allowed_from_manifest() -> set[str]:
        if not MANIFEST_PATH.exists():
            return set()
        manifest = json.loads(MANIFEST_PATH.read_text())
        tables = set()
        for node in manifest.get("nodes", {}).values():
            if node.get("resource_type") != "model":
                continue
            if node.get("config", {}).get("materialized") != "table":
                continue
            tables.add(node["name"])
        return tables

    def table_refs(self, sql: str) -> list[tuple[str, str, str]]:
        return _TABLE_REF.findall(sql or "")

    def _assert_gold_only(self, sql: str) -> None:
        for project, dataset, table in self.table_refs(sql):
            if dataset.upper() == "INFORMATION_SCHEMA":
                continue
            if not dataset.endswith("_gold"):
                raise PermissionError(
                    f"只能查询 gold 层，检测到 {project}.{dataset}.{table}。"
                    f"可用表：{', '.join(sorted(self.allowed)) or '(manifest 未生成)'}"
                )
            if self.allowed and table not in self.allowed:
                raise PermissionError(
                    f"{table} 不是已发布的 mart。可用表：{', '.join(sorted(self.allowed))}"
                )

    # -- execution ----------------------------------------------------------
    def run(self, sql: str, max_rows: Optional[int] = None) -> pd.DataFrame:
        self._assert_gold_only(sql)
        return super().run(sql, max_rows=max_rows)

    def estimate(self, sql: str):
        self._assert_gold_only(sql)
        return super().estimate(sql)

    # -- freshness ----------------------------------------------------------
    def freshness(self) -> list[dict]:
        """Latest load time per mart, from the pipeline-written freshness table."""
        sql = (
            f"select table_name, last_loaded, row_count "
            f"from `{self.billing_project}.{self.meta_dataset}.mart_freshness` "
            f"order by table_name"
        )
        df = BigQueryDataSource.run(self, sql, max_rows=50)
        out = []
        for row in df.to_dict("records"):
            out.append(
                {
                    "table": row["table_name"],
                    "last_loaded": str(row["last_loaded"]),
                    "row_count": int(row["row_count"] or 0),
                }
            )
        return out

    def freshness_text(self) -> str:
        try:
            rows = self.freshness()
        except Exception as exc:  # never block a chat on a metadata hiccup
            return f"（新鲜度不可用：{type(exc).__name__}）"
        if not rows:
            return "（无已发布的 mart）"
        return "；".join(f"{r['table']}：{r['last_loaded']}（{r['row_count']} 行）" for r in rows)
