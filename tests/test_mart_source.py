"""MartSource: the copilot may only read published gold tables."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.mart_source import MartSource  # noqa: E402


@pytest.fixture()
def src(tmp_path, monkeypatch):
    manifest = {
        "nodes": {
            "model.data_copilot.mart_municipality_population": {
                "resource_type": "model",
                "name": "mart_municipality_population",
                "config": {"materialized": "table"},
            },
            "model.data_copilot.stg_kpi_values": {
                "resource_type": "model",
                "name": "stg_kpi_values",
                "config": {"materialized": "view"},
            },
        }
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest))
    monkeypatch.setenv("DBT_MANIFEST", str(p))
    import src.core.mart_source as ms

    monkeypatch.setattr(ms, "MANIFEST_PATH", p)
    return MartSource(project="proj", dataset="dev_gold")


def test_allowed_tables_come_from_manifest(src):
    assert src.allowed == {"mart_municipality_population"}


@pytest.mark.parametrize(
    "sql",
    [
        "select * from `proj.dev_silver.stg_kpi_values`",
        "select * from proj.dev_bronze.kolada_kpi_values",
        "with x as (select * from `proj.dev_gold.unknown_mart`) select * from x",
    ],
)
def test_blocks_non_gold_and_unknown(src, sql):
    with pytest.raises(PermissionError):
        src.run(sql)


def test_gold_reference_passes_the_guard(src):
    # passes the guard, then fails later at the network layer — assert the guard itself
    src._assert_gold_only("select * from `proj.dev_gold.mart_municipality_population`")


def test_information_schema_is_allowed(src):
    src._assert_gold_only("select * from `proj.INFORMATION_SCHEMA.TABLES`")
