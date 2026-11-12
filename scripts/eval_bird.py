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
COHORT_ROOT = BIRD_ROOT / "cohorts"
DB_ROOT = BIRD_ROOT / "minidev" / "MINIDEV" / "dev_databases"
COHORT_SIZES = {"historical": 50, "development": 100, "blind": 50}


# ── data ---------------------------------------------------------------------
def load_questions(limit: int = 0, db_id: str = "", stratify: bool = True) -> list[dict]:
    """Questions in question_id order, or proportional to each database.

    Sorting by id and taking the first N is not a sample: Mini-Dev's ids are
    grouped by database, so the first 50 questions are 49 questions about
    california_schools. Proportional allocation spreads the sample across all 11
    databases, which is what makes a failure breakdown meaningful.
    """
    from collections import defaultdict

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
    if not limit or not stratify:
        return rows[:limit] if limit else rows

    return stratify_rows(rows, limit)


def _rows_for_ids(rows: list[dict], ids: list[int]) -> list[dict]:
    by_id = {r["id"]: r for r in rows}
    missing = [qid for qid in ids if qid not in by_id]
    if missing:
        raise ValueError(f"cohort refers to unavailable question ids: {missing[:5]}")
    return [by_id[qid] for qid in ids]


def cohort_manifest_path(cohort: str) -> Path:
    if cohort not in COHORT_SIZES:
        raise ValueError(f"unknown cohort: {cohort}")
    return COHORT_ROOT / f"{cohort}.json"


def build_cohort_manifests(rows: list[dict]) -> dict[str, dict]:
    """Allocate three disjoint, stratified cohorts without looking at SQL results.

    The historical cohort reproduces the first 50-question experiment. Development
    may be inspected during tuning; blind is reserved for exactly one frozen run.
    """
    remaining = list(rows)
    manifests: dict[str, dict] = {}
    for cohort, size in COHORT_SIZES.items():
        picked = stratify_rows(remaining, size)
        ids = [r["id"] for r in picked]
        manifests[cohort] = {
            "benchmark": "BIRD Mini-Dev sqlite",
            "cohort": cohort,
            "selection": "deterministic stratified, disjoint sequential allocation",
            "question_ids": ids,
        }
        picked_ids = set(ids)
        remaining = [r for r in remaining if r["id"] not in picked_ids]
    return manifests


def write_cohort_manifests(rows: list[dict]) -> dict[str, Path]:
    COHORT_ROOT.mkdir(parents=True, exist_ok=True)
    manifests = build_cohort_manifests(rows)
    paths: dict[str, Path] = {}
    for name, manifest in manifests.items():
        path = cohort_manifest_path(name)
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        paths[name] = path
    return paths


def load_cohort(rows: list[dict], cohort: str) -> list[dict]:
    path = cohort_manifest_path(cohort)
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path}; create frozen manifests with --write-cohort-manifests"
        )
    manifest = json.loads(path.read_text())
    if manifest.get("cohort") != cohort:
        raise ValueError(f"cohort manifest mismatch in {path}")
    selected = _rows_for_ids(rows, manifest.get("question_ids", []))
    expected = COHORT_SIZES[cohort]
    if len(selected) != expected:
        raise ValueError(f"{cohort} cohort has {len(selected)} questions; expected {expected}")
    return selected


def stratify_rows(rows: list[dict], limit: int) -> list[dict]:
    """Proportional pick per database, evenly spaced inside each one."""
    from collections import defaultdict

    by_db: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_db[r["db"]].append(r)

    total = len(rows)
    # Largest-remainder allocation: every database that exists in the sample is
    # represented at least once, and the rest are proportional.
    quotas = {db: max(1, round(len(items) * limit / total)) for db, items in by_db.items()}
    while sum(quotas.values()) > limit:
        biggest = max(quotas, key=lambda d: quotas[d] - len(by_db[d]) * limit / total)
        if quotas[biggest] <= 1:
            break
        quotas[biggest] -= 1
    while sum(quotas.values()) < limit:
        remain = [d for d in by_db if quotas[d] < len(by_db[d])]
        if not remain:
            break
        quotas[max(remain, key=lambda d: len(by_db[d]))] += 1

    picked: list[dict] = []
    for db, items in by_db.items():
        # Evenly spaced picks inside a database, so one library is not dominated
        # by its first few easy questions.
        q = quotas[db]
        step = max(1, len(items) // q)
        picked.extend(items[::step][:q])
    return sorted(picked, key=lambda r: r["id"])[:limit]


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
    from src.core import schema_link, tracing
    from src.core.agent_loop import run_agent_loop
    from src.core.agent_tools import create_agent_tools
    from src.core.datasource import SqlDataSource

    path = db_path(row["db"])
    started = time.time()

    # The eval bypasses MartSource on purpose: BIRD needs an arbitrary SQLite
    # file, which is exactly what the governed product forbids at runtime.
    ds = SqlDataSource(f"sqlite:///{path}")
    schema, tool_map = create_agent_tools(source=ds, benchmark_question=build_question(row))

    # Schema linking: narrow the schema the agent sees before it writes SQL.
    # A bad shortlist would be worse than none, so the full schema is restored on
    # any lookup failure and the run record says whether it was used.
    full_schema_chars = len(ds.schema_json())
    link_info = {
        "used": False, "tables": [], "initial_tables": [], "added_tables": [],
        "fell_back": False, "dropped_tables": [],
        "full_schema_chars": full_schema_chars, "narrowed_schema_chars": full_schema_chars,
        "link_response_chars": 0,
    }
    if schema_link.enabled():
        question_text = build_question(row)
        full = ds.schema_json()
        links = schema_link.select_links(full, question_text)
        link_info["link_response_chars"] = int(links.get("raw_chars", 0)) if links else 0
        check = schema_link.verify_links(full, links) if links else {"ok": False, "tables": [], "dropped": []}
        if check["ok"]:
            # A hallucinated table name is dropped, not fatal: the rest of the
            # shortlist is usually right.
            links = {"tables": check["tables"],
                     "columns": {t: c for t, c in (links.get("columns") or {}).items()
                                 if t in check["tables"]}}
            expanded = schema_link.expand_fk_neighbors(full, links)
            # In BIRD, a wrong column shortlist is as damaging as a wrong table
            # shortlist. Keep complete columns for the relevant FK-connected
            # subgraph; this still cuts whole unrelated tables from the prompt.
            links = {"tables": expanded["tables"], "columns": {}}
            narrowed = schema_link.apply_links(full, links)
            link_info["narrowed_schema_chars"] = len(narrowed)
            if narrowed != full:
                for name, fn in list(tool_map.items()):
                    if name == "get_database_schema":
                        tool_map[name] = _fixed_schema(narrowed, fn)
                link_info.update(used=True, tables=links["tables"],
                                 initial_tables=check["tables"],
                                 added_tables=expanded["added_tables"],
                                 dropped_tables=check["dropped"])
        else:
            link_info["fell_back"] = True

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
        "linked": link_info["used"],
        "linked_tables": link_info["tables"],
        "link_initial_tables": link_info["initial_tables"],
        "link_added_tables": link_info["added_tables"],
        "link_fell_back": link_info["fell_back"],
        "link_dropped": link_info.get("dropped_tables", []),
        "full_schema_chars": link_info["full_schema_chars"],
        "narrowed_schema_chars": link_info["narrowed_schema_chars"],
        "link_response_chars": link_info["link_response_chars"],
        "sql_count": len(ds.executed_sql),
        "reply_chars": len(reply or ""),
    }


def _fixed_schema(narrowed: str, original):
    """Serve the linked schema, but let force_refresh fall back to the full one.

    The agent may decide the shortlist is wrong and ask again; that request must
    reach the real tool rather than silently returning the same subset.
    """
    def tool(force_refresh: bool = False):
        if force_refresh:
            return original(force_refresh=True)
        return narrowed
    return tool


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


def summarize_linking(results: list[dict]) -> dict:
    """Summarise linker telemetry without assuming every agent run completed.

    A provider/agent exception is still a scored result. Its diagnostic record
    intentionally contains no schema-link fields, so report writing must never
    turn that one failure into a lost, costly whole run.
    """
    n = len(results)
    return {
        "used": sum(1 for r in results if r.get("linked", False)),
        "fell_back": sum(1 for r in results if r.get("link_fell_back", False)),
        "average_full_schema_chars": round(
            sum(r.get("full_schema_chars", 0) for r in results) / n, 1
        ) if n else 0,
        "average_narrowed_schema_chars": round(
            sum(r.get("narrowed_schema_chars", 0) for r in results) / n, 1
        ) if n else 0,
    }


# ── main ---------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--cohort", choices=sorted(COHORT_SIZES),
                    help="use a frozen, disjoint benchmark cohort (ignores --limit)")
    ap.add_argument("--write-cohort-manifests", action="store_true",
                    help="create the frozen cohort manifests, then exit without model calls")
    ap.add_argument("--db", default="", help="restrict to one database id")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--sql-timeout", type=float, default=12.0)
    ap.add_argument("--schema-linking", action="store_true",
                    help="pick relevant tables/columns with one extra call before SQL")
    ap.add_argument("--no-shape-verification", action="store_true",
                    help="disable BIRD's final SQL shape check (enabled by default)")
    ap.add_argument("--no-value-grounding", action="store_true",
                    help="disable BIRD's distinct-value lookup tool (enabled by default)")
    ap.add_argument("--no-sql-repair", action="store_true",
                    help="disable BIRD's execution-error repair instruction (enabled by default)")
    ap.add_argument("--only-ids", nargs="+", type=int, default=None,
                    help="run exactly these question ids (re-run a known failure set)")
    ap.add_argument("--no-stratify", action="store_true",
                    help="take the first N by question_id instead of sampling per database")
    args = ap.parse_args()

    # Benchmark mode: minimal prompt, more iterations. Set before the loop reads
    # them, and reported in the run record so the number is reproducible.
    os.environ.setdefault("AGENT_PROMPT", "bird")
    os.environ.setdefault("AGENT_MAX_ITERATIONS", "12")
    for env_name, disabled in (
        ("BIRD_VERIFY_SHAPE", args.no_shape_verification),
        ("BIRD_VALUE_GROUNDING", args.no_value_grounding),
        ("BIRD_SQL_REPAIR", args.no_sql_repair),
    ):
        if disabled:
            os.environ[env_name] = "off"
        else:
            os.environ.setdefault(env_name, "on")
    if args.schema_linking:
        os.environ["SCHEMA_LINKING"] = "on"

    from src.core import schema_link
    from src.core.tracing import (agent_thinking_enabled, linker_thinking_enabled,
                                  llm_base_url, llm_model)

    all_rows = load_questions(0, args.db, stratify=False)
    if args.write_cohort_manifests:
        if args.db:
            ap.error("--write-cohort-manifests cannot be combined with --db")
        paths = write_cohort_manifests(all_rows)
        for cohort, path in paths.items():
            print(f"wrote {cohort}: {path}")
        return 0
    if args.only_ids:
        # Re-running a fixed set of failures is the cheapest useful experiment:
        # it answers "did this change help?" without paying for questions that
        # already pass.
        by_id = {r["id"]: r for r in all_rows}
        missing = [i for i in args.only_ids if i not in by_id]
        if missing:
            ap.error(f"unknown question ids: {missing[:5]}")
        rows = [by_id[i] for i in args.only_ids]
    elif args.cohort:
        rows = load_cohort(all_rows, args.cohort)
    else:
        rows = stratify_rows(all_rows, args.limit) if not args.no_stratify else all_rows[:args.limit]
    print(f"model    {llm_model()}  (agent thinking: {'on' if agent_thinking_enabled() else 'off'}; "
          f"linker thinking: {'on' if linker_thinking_enabled() else 'off'})")
    print(f"prompt   {os.getenv('AGENT_PROMPT')}   max_iterations {os.getenv('AGENT_MAX_ITERATIONS')}")
    print(f"questions {len(rows)}  workers {args.workers}"
          + (f"  cohort {args.cohort}" if args.cohort else ""))
    from collections import Counter
    counts = Counter(r["db"] for r in rows)
    print(f"databases {len(counts)}  {'(stratified)' if not args.no_stratify else '(first N)'}")
    print("  " + ", ".join(f"{db}:{n}" for db, n in counts.most_common()))
    print(f"schema_linking {'on' if args.schema_linking else 'off'}")
    print("benchmark_guards " + ", ".join(
        f"{name}={'on' if os.getenv(env, 'off').lower() in {'1', 'on', 'true', 'yes'} else 'off'}"
        for name, env in (("shape", "BIRD_VERIFY_SHAPE"),
                          ("values", "BIRD_VALUE_GROUNDING"),
                          ("repair", "BIRD_SQL_REPAIR"))
    ))

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
        "agent_thinking": agent_thinking_enabled(),
        "linker_thinking": linker_thinking_enabled(),
        "llm_base_url": llm_base_url(),
        "agent_prompt": os.getenv("AGENT_PROMPT"),
        "agent_max_iterations": int(os.getenv("AGENT_MAX_ITERATIONS", "7")),
        "schema_linking": schema_link.enabled(),
        "bird_verify_shape": os.getenv("BIRD_VERIFY_SHAPE", "off").lower() in {"1", "on", "true", "yes"},
        "bird_value_grounding": os.getenv("BIRD_VALUE_GROUNDING", "off").lower() in {"1", "on", "true", "yes"},
        "bird_sql_repair": os.getenv("BIRD_SQL_REPAIR", "off").lower() in {"1", "on", "true", "yes"},
        "limit": args.limit,
        "cohort": args.cohort,
        "cohort_manifest": str(cohort_manifest_path(args.cohort)) if args.cohort else None,
        "question_ids": [r["id"] for r in rows],
        "workers": args.workers,
        "commit": _git_sha(),
        "ex": round(ex, 4),
        "samples": len(results),
        "per_db": {db: {"ok": sum(1 for r in items if r["ok"]), "n": len(items)}
                   for db, items in by_db.items()},
        "failures": fails,
        "linking": summarize_linking(results),
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
