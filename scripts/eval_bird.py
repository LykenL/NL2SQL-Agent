"""BIRD Mini-Dev runner: Executive Accuracy over real SQLite databases.

Why this exists next to evals/marts_core.json: that suite measures the governed
product (gold-only boundary, semantic layer). BIRD measures the base text-to-SQL
capability on 11 public databases, which is the only number that can be compared
with published results. They answer different questions and are kept separate.

The scoring follows BIRD's definition: execute the predicted SQL and the gold SQL
on the same database and compare RESULT SETS, not SQL text — ignoring row order,
using a tolerance for floats, and treating an empty result as a legitimate answer.

Usage:
    python3 scripts/eval_bird.py --limit 10 --db student_club
    python3 scripts/eval_bird.py --limit 50 --workers 8
    python3 scripts/eval_bird.py --limit 500 --workers 8 --out evals/bird/results/run.json

Cost control: --limit picks the first N questions (sorted by question_id);
--dry-run prints the plan and the schemas' token cost without calling the model.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

BIRD_ROOT = ROOT / "evals" / "bird"
DB_ROOT = BIRD_ROOT / "minidev" / "MINIDEV" / "dev_databases"


# ── data ---------------------------------------------------------------------
def load_questions(limit: int = 0, db_id: str = "") -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset("birdsql/bird_mini_dev", split="mini_dev_sqlite")
    rows = sorted(
        ({"id": r["question_id"], "db": r["db_id"], "question": r["question"],
          "evidence": r["evidence"], "sql": r["SQL"], "difficulty": r["difficulty"]}
         for r in ds),
        key=lambda r: r["id"],
    )
    if db_id:
        rows = [r for r in rows if r["db"] == db_id]
    return rows[:limit] if limit else rows


def db_path(db_id: str) -> str:
    return str(DB_ROOT / db_id / f"{db_id}.sqlite")


# ── execution + comparison ---------------------------------------------------
def run_sql(path: str, sql: str, timeout: float = 12.0) -> tuple[Optional[list], Optional[str]]:
    """Execute read-only. Returns (rows, None) or (None, error)."""
    if not os.path.exists(path):
        return None, f"database not found: {path}"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
    except sqlite3.Error as exc:
        return None, f"connect failed: {exc}"
    try:
        con.set_progress_handler(lambda: 0, 1_000_000)  # cooperative interrupt point
        cur = con.execute(sql)
        return cur.fetchall(), None
    except Exception as exc:  # syntax errors, timeouts, missing tables
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        con.close()


def _norm_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    text = str(value).strip()
    # BIRD data mixes types across sources: "1.0" and 1 are the same answer.
    try:
        return round(float(text), 6)
    except ValueError:
        return text.lower()


def result_matches(gold: list, predicted: list, tol: float = 1e-3) -> bool:
    """Multiset comparison of result sets, the way BIRD's EX metric works."""
    if gold is None or predicted is None:
        return False
    if len(gold) != len(predicted):
        return False

    def key(row):
        return tuple(_norm_cell(c) for c in row)

    a = sorted(key(r) for r in gold)
    b = sorted(key(r) for r in predicted)
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            return False
        for x, y in zip(ra, rb):
            if isinstance(x, float) and isinstance(y, float):
                if abs(x - y) > tol * max(1.0, abs(x)):
                    return False
            elif x != y:
                return False
    return True


def build_question(row: dict) -> str:
    """BIRD ships an `evidence` hint; it belongs in the prompt."""
    q = row["question"].strip()
    if row.get("evidence"):
        q += f"\n\nHint: {row['evidence'].strip()}"
    return q


# ── one question -------------------------------------------------------------
def evaluate_one(row: dict, sql_timeout: float = 12.0) -> dict:
    from src.core import tracing
    from src.core.agent_loop import run_agent_loop
    from src.core.agent_tools import create_agent_tools
    from src.core.datasource import SqlDataSource

    path = db_path(row["db"])
    started = time.time()

    # The eval bypasses MartSource on purpose: BIRD needs an arbitrary SQLite
    # file, which is exactly what the governed product forbids at runtime.
    ds = SqlDataSource(f"sqlite:///{path}")
    schema, tool_map = create_agent_tools(source=ds)

    used: list[str] = []
    for name, fn in list(tool_map.items()):
        def wrap(*a, _n=name, _f=fn, **k):
            used.append(_n)
            return _f(*a, **k)
        tool_map[name] = wrap

    trace: dict = {}
    try:
        reply, script, _, seconds = run_agent_loop(
            build_question(row), f"bird_{row['id']}_{uuid.uuid4().hex[:6]}",
            schema, tool_map, db_uri=ds.uri, trace=trace, data_source=ds,
        )
    except Exception as exc:
        return {**row, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                "failure": "error", "seconds": round(time.time() - started, 1)}

    gold_rows, gold_err = run_sql(path, row["sql"], sql_timeout)

    # The agent's final script is its stated answer; the last executed statement is
    # the fallback. Whichever runs cleanly first is the prediction — an agent that
    # ends on a sanity-check query should not be scored on that query.
    candidates = []
    submitted = getattr(ds, "submitted_sql", None)
    if submitted:
        candidates.append(submitted)
    from_script = extract_sql(script)
    if from_script:
        candidates.append(from_script)
    if ds.executed_sql:
        candidates.append(ds.executed_sql[-1])

    predicted, pred_rows, pred_err = "", None, "no SQL produced"
    for cand in candidates:
        rows, err = run_sql(path, cand, sql_timeout)
        predicted, pred_rows, pred_err = cand, rows, err
        if err is None:
            break

    has_sql = bool(ds.executed_sql or script)
    ok = (gold_err is None and pred_rows is not None
          and result_matches(gold_rows, pred_rows))
    return {
        **row,
        "ok": ok,
        "failure": None if ok else classify(gold_err, pred_err, pred_rows, gold_rows,
                                            "x" if has_sql else ""),
        "gold_error": gold_err,
        "pred_error": pred_err,
        "gold_n": len(gold_rows) if gold_rows is not None else None,
        "pred_n": len(pred_rows) if pred_rows is not None else None,
        "iterations": int(trace.get("iterations", 0)),
        "tool_calls": len(used),
        "seconds": round(seconds, 1),
        "predicted_sql": (predicted or "")[:800],
        "submitted": bool(submitted),
        "sql_count": len(ds.executed_sql),
        "reply_chars": len(reply or ""),
    }


def extract_sql(script: str) -> str:
    """Pull the SQL string out of the generated Python, without executing it."""
    import re

    # ds.run("SELECT ...") / ds.run('...') / run_query(sql="...")
    for pattern in (r'ds\.run\(\s*[fr]?["\']([\s\S]*?)["\']\s*,',
                    r'ds\.run\(\s*[fr]?["\']([\s\S]*?)["\']\s*\)',
                    r'run_query\(\s*[^,]*?"([\s\S]*?)"'):
        m = re.search(pattern, script)
        if m:
            return m.group(1).replace('\\"', '"').replace("\\n", "\n").strip()
    # Fallback: a bare SQL string assigned to a variable.
    m = re.search(r'["\']((?:SELECT|WITH)\b[\s\S]*?)["\']', script, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def classify(gold_err, pred_err, pred_rows, gold_rows, script) -> str:
    if not script:
        return "no_sql"
    if pred_err and "syntax" in pred_err.lower():
        return "sql_syntax"
    if pred_err:
        return "sql_error"
    if gold_err:
        return "gold_broken"
    return "wrong_result"


# ── main ---------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--db", default="", help="restrict to one database id")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--sql-timeout", type=float, default=12.0)
    args = ap.parse_args()

    # Benchmark mode: minimal prompt, more iterations. Set before the loop reads
    # them, and reported in the run record so the number is reproducible.
    os.environ.setdefault("AGENT_PROMPT", "bird")
    os.environ.setdefault("AGENT_MAX_ITERATIONS", "12")

    from src.core.tracing import llm_model, thinking_enabled

    rows = load_questions(args.limit, args.db)
    print(f"model    {llm_model()}  (thinking: {'on' if thinking_enabled() else 'off'})")
    print(f"prompt   {os.getenv('AGENT_PROMPT')}   max_iterations {os.getenv('AGENT_MAX_ITERATIONS')}")
    print(f"questions {len(rows)}  workers {args.workers}")
    print(f"databases {len({r['db'] for r in rows})}")

    if args.dry_run:
        for r in rows[:10]:
            print(f"  {r['id']:>4} {r['db']:<24} {r['difficulty']:<12} {r['question'][:60]}")
        if len(rows) > 10:
            print(f"  ... {len(rows) - 10} more")
        print("\ndry run — no model calls")
        return 0

    from src.core import tracing

    tracing.client()
    started = time.time()
    results: list[dict] = []

    if args.workers <= 1:
        for i, row in enumerate(rows, 1):
            r = evaluate_one(row, args.sql_timeout)
            results.append(r)
            print(f"[{i}/{len(rows)}] {r['id']} {r['db'][:18]:<18} "
                  f"{'OK  ' if r['ok'] else 'FAIL'} {r.get('failure') or ''}")
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(evaluate_one, row, args.sql_timeout): row for row in rows}
            for done, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                results.append(r)
                print(f"[{done}/{len(rows)}] {r['id']} {r['db'][:18]:<18} "
                      f"{'OK  ' if r['ok'] else 'FAIL'} {r.get('failure') or ''}")

    results.sort(key=lambda r: r["id"])
    ex = sum(1 for r in results if r["ok"]) / len(results) if results else 0.0
    by_db: dict[str, list] = {}
    for r in results:
        by_db.setdefault(r["db"], []).append(r)

    print("\n" + "=" * 68)
    print("BIRD MINI-DEV — EXECUTION ACCURACY")
    print("=" * 68)
    print(f"  questions   {len(results)}")
    sub = sum(1 for r in results if r.get("submitted"))
    print(f"  EX          {ex:.3f}   ({sum(1 for r in results if r['ok'])}/{len(results)})")
    print(f"  submitted   {sub}/{len(results)}  (submit_query used)")
    print(f"  wall        {round(time.time() - started, 1)}s")
    print("\n  per database")
    for db, items in sorted(by_db.items(), key=lambda kv: -len(kv[1])):
        ok = sum(1 for r in items if r["ok"])
        print(f"    {db:<26} {ok:>3}/{len(items):<3} {ok/len(items):.2f}")
    fails: dict[str, int] = {}
    for r in results:
        if not r["ok"]:
            fails[r.get("failure") or "unknown"] = fails.get(r.get("failure") or "unknown", 0) + 1
    if fails:
        print("\n  failures")
        for k, v in sorted(fails.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<16} {v}")

    record = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "benchmark": "BIRD Mini-Dev (sqlite split)",
        "model": llm_model(),
        "thinking": thinking_enabled(),
        "limit": args.limit,
        "workers": args.workers,
        "commit": _git_sha(),
        "ex": round(ex, 4),
        "samples": len(results),
        "per_db": {db: {"ok": sum(1 for r in items if r["ok"]), "n": len(items)}
                   for db, items in by_db.items()},
        "failures": fails,
        "wall_seconds": round(time.time() - started, 1),
        "results": results,
    }
    out = Path(args.out) if args.out else BIRD_ROOT / "results" / f"run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nreport  {out}")
    tracing.flush()
    return 0


def _git_sha() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
