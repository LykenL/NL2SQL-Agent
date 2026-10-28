#!/usr/bin/env python3
"""
Definition-variance probe (the gate for the semantic layer).

Asks the same business question in N phrasings, M times each, and records for
every run:
  - which physical columns the generated script aggregated
  - the numeric answer printed by that script
Use it to decide whether a semantic registry is worth building for a given
database: if the column choice and the numbers never diverge, the ambiguity the
registry is meant to remove does not exist.

    python scripts/probe_definition_variance.py --runs 1 --phrasings 5
    python scripts/probe_definition_variance.py --runs 3 --out probe.json
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.agent_loop import run_agent_loop  # noqa: E402
from src.core.agent_tools import create_agent_tools  # noqa: E402
from src.core.executor import safe_execute  # noqa: E402

DEFAULT_DB = "sqlite:////Users/lykenl/Documents/local_agent/data_copilot/databases/Chinook_Sqlite.sqlite"

PHRASINGS = [
    "Chinook 数据库的营收总额是多少？只要一个数字。",
    "这个库的销售额总共多少？只要一个数字。",
    "帮我算一下总收入，给个总数就行。",
    "What is the total revenue in this database? One number only.",
    "卖唱片一共卖了多少钱？直接给数字。",
]

COLUMN_SIGNALS = ["UnitPrice", "Quantity", "Total"]


def columns_used(script: str) -> list[str]:
    toks = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", script or ""))
    return [c for c in COLUMN_SIGNALS if c in toks]


def numeric_answer(text: str) -> float | None:
    nums = []
    for raw in re.findall(r"[-+]?\d[\d,]*\.?\d*", text or ""):
        try:
            nums.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    # revenue answers on this database are in the thousands; ignore row counts
    big = [n for n in nums if n >= 100]
    return big[-1] if big else (nums[-1] if nums else None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--runs", type=int, default=1, help="runs per phrasing")
    ap.add_argument("--phrasings", type=int, default=len(PHRASINGS))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    tools, tool_map = create_agent_tools(args.db)
    runs = []

    for i, question in enumerate(PHRASINGS[: args.phrasings], 1):
        for r in range(args.runs):
            session = f"probe_{i}_{r}_{int(time.time())}"
            started = time.time()
            try:
                text, script, _, _ = run_agent_loop(question, session, tools, tool_map, db_uri=args.db)
            except Exception as exc:  # a failed run is still a data point
                runs.append({"phrasing": i, "run": r, "question": question,
                             "error": str(exc), "value": None, "columns": []})
                print(f"[{i}.{r}] ERROR {exc}")
                continue

            value = numeric_answer(text)
            if value is None and script:
                try:
                    value = numeric_answer(safe_execute(script, {"db_uri": args.db, "DATABASE_URI": args.db}))
                except Exception:
                    value = None

            record = {
                "phrasing": i, "run": r, "question": question,
                "value": value, "columns": columns_used(script),
                "elapsed_s": round(time.time() - started, 1),
                "script_head": (script or "").strip().splitlines()[:2],
            }
            runs.append(record)
            print(f"[{i}.{r}] value={value} columns={record['columns']} {record['elapsed_s']}s")

    values = [r["value"] for r in runs if r.get("value") is not None]
    col_sets = {tuple(sorted(r["columns"])) for r in runs if r.get("columns")}
    spread = (
        round((max(values) - min(values)) / max(values) * 100, 2)
        if len(values) > 1 and max(values) else 0.0
    )
    # Answers are what the business user sees: divergence there is the only
    # thing that justifies a registry. Differing column paths that land on the
    # same number are reported, but they are not an ambiguity.
    if len(values) < 2:
        verdict = "inconclusive — need more successful runs"
    elif spread < 1.0:
        verdict = "answers agree across phrasings — a registry would not change any number here"
    else:
        verdict = f"answers diverge ({spread}% spread) — phrasings resolve to different definitions"

    summary = {
        "runs": len(runs),
        "with_value": len(values),
        "distinct_values": sorted({round(v, 2) for v in values}),
        "value_spread_pct": spread,
        "distinct_column_sets": ["+".join(c) or "(none)" for c in col_sets],
        "verdict": verdict,
    }

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"summary": summary, "runs": runs}, f, indent=2, ensure_ascii=False)
        print(f"written: {args.out}")


if __name__ == "__main__":
    main()
