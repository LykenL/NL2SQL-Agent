"""
Bronze ingestion: Kolada open API (Swedish municipal KPIs) and SCB population.

Bronze semantics for API sources: one *snapshot* table per source, replaced on
every run (Kolada is small and the API is cheap to re-pull). Every row carries
`ingested_at`, so silver can dedupe and the UI can show freshness.
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import requests
from dagster import asset
from google.cloud import bigquery

PROJECT = os.getenv("BQ_PROJECT", "woven-surface-511317-g0")
PREFIX = os.getenv("BQ_PREFIX", "data_copilot_dev")
BRONZE = f"{PREFIX}_bronze"

KOLADA = "https://api.kolada.se/v3"

# Verified against the live API; extend freely — the pipeline is KPI-agnostic.
KPI_IDS = os.getenv("KOLADA_KPIS", "N01951,N00003").split(",")
YEARS = [int(y) for y in os.getenv("KOLADA_YEARS", "2022,2023,2024").split(",")]


def _bq() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


def _replace(df: pd.DataFrame, table: str) -> None:
    cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", autodetect=True)
    job = _bq().load_table_from_dataframe(df, f"{PROJECT}.{BRONZE}.{table}", job_config=cfg)
    job.result()
    print(f"[bronze] {table}: {len(df)} rows -> {PROJECT}.{BRONZE}.{table}")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@asset(group_name="bronze")
def kolada_municipalities() -> None:
    """Dimension snapshot: every municipality / region / district Kolada knows."""
    raw = requests.get(f"{KOLADA}/municipality", timeout=60).json()["values"]
    df = pd.DataFrame(
        [{"municipality_code": r["id"], "municipality_name": r["title"], "municipality_type": r["type"]} for r in raw]
    )
    df["ingested_at"] = _now()
    _replace(df, "kolada_municipalities")


@asset(group_name="bronze")
def kolada_kpi_catalog() -> None:
    """The KPI dictionary — what each metric means (feeds the semantic layer later)."""
    raw = requests.get(f"{KOLADA}/kpi?from_year={min(YEARS)}&to_year={max(YEARS)}", timeout=90).json()["values"]
    df = pd.DataFrame(
        [
            {
                "kpi_id": k["id"],
                "title": k.get("title", ""),
                "description": (k.get("description") or "")[:2000],
                "operating_area": k.get("operating_area", ""),
                "is_divided_by_gender": bool(k.get("is_divided_by_gender", False)),
            }
            for k in raw
        ]
    )
    df["ingested_at"] = _now()
    _replace(df, "kolada_kpi_catalog")


@asset(group_name="bronze")
def kolada_kpi_values() -> None:
    """Long-format facts: one row per (kpi, year, municipality, gender)."""
    rows: list[dict] = []
    ingested = _now()

    for kpi in KPI_IDS:
        for year in YEARS:
            resp = requests.get(f"{KOLADA}/data/kpi/{kpi}/year/{year}", timeout=90)
            if resp.status_code != 200:
                print(f"[bronze] {kpi}/{year}: HTTP {resp.status_code}, skipped")
                continue
            for entry in resp.json().get("values", []):
                for v in entry.get("values", []):
                    rows.append(
                        {
                            "kpi_id": entry["kpi"],
                            "period": int(entry["period"]),
                            "municipality_code": entry["municipality"],
                            "gender": v.get("gender", "T"),
                            "value": v.get("value"),
                            "status": v.get("status", ""),
                            "is_deleted": bool(v.get("isdeleted", False)),
                            "ingested_at": ingested,
                        }
                    )
            print(f"[bronze] {kpi}/{year}: {len(resp.json().get('values', []))} municipalities")

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("Kolada returned no values — check KPI_IDS / YEARS")
    _replace(df, "kolada_kpi_values")
