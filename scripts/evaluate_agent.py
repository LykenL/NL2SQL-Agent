"""Agent evaluation over the governed marts.

What this measures, and why in this order:

  accuracy        numeric cases — the expected values come from SQL against the
                  warehouse, never from a number typed by hand
  guard_rate      the agent must REFUSE silver/bronze. This is the product's
                  differentiating property, so it is a hard gate: < 100% fails
  tool_choice     when a question needs a specific tool (e.g. DQ triage), did it
                  call that tool?
  efficiency      agent iterations and wall time per case. A correct answer that
                  took six retries still costs money and signals a broken tool.

Design notes:
  - Expected values are computed with `expect_sql` at run time, so a case cannot
    rot silently when the data refreshes.
  - Every case runs in a fresh session id: memory replay would make results
    incomparable between runs.
  - The run records model, data source, iterations and tokens. Comparing two runs
    without those columns compares nothing.
  - `--dry-run` prints the resolved expectations (and their cost in bytes) without
    calling the model at all. Use it after editing cases.

Usage:
    python3 scripts/evaluate_agent.py --dataset evals/marts_core.json --dry-run
    python3 scripts/evaluate_agent.py --dataset evals/marts_core.json
    python3 scripts/evaluate_agent.py --dataset evals/marts_core.json --only pop_top3_2024
    python3 scripts/evaluate_agent.py --dataset evals/marts_core.json --max-cases 3

Exit code is 0 only when accuracy >= --min-accuracy AND guard_rate == 1.0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from src.core.datasource import source_from_spec  # noqa: E402

DEFAULT_SOURCE = "marts:woven-surface-511317-g0.data_copilot_dev_gold"


# ── dataset model ────────────────────────────────────────────────────────────
@dataclass
class Case:
    id: str
    question: str
    kind: str  # numbers | refusal | tool_used
    expect_sql: Optional[str] = None
    expect: Optional[list[Any]] = None          # numbers: expected cell values
    tolerance: float = 0.0                       # numbers: relative tolerance
    expect_error_contains: Optional[str] = None  # refusal: substring in the reply
    expect_tool: Optional[str] = None            # tool_used: tool name
    note: str = ""


@dataclass
class Result:
    case_id: str
    kind: str
    passed: bool
    detail: str
    iterations: int = 0
    seconds: float = 0.0
    tool_calls: int = 0
    blocked: int = 0
    expected: Any = None
    actual: Any = None


# ── expectation resolution ───────────────────────────────────────────────────
def _norm(value: Any) -> Any:
    """Normalise a warehouse value for comparison (numbers as floats)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text.replace(",", "").replace("%", ""))
    except ValueError:
        return text.lower()


def resolve_expectation(case: Case, ds) -> list[Any]:
    """Run expect_sql against the warehouse; the SQL is the source of truth."""
    if not case.expect_sql:
        return case.expect or []
    rows = ds.run(case.expect_sql, max_rows=100).to_dict("records")
    if not rows:
        return []
    column = case.expect[0] if case.expect else list(rows[0].keys())[0]
    if isinstance(column, str) and column in rows[0]:
        return [row[column] for row in rows]
    # No column named by the case: return every cell of the first column.
    first = next(iter(rows[0]))
    return [row[first] for row in rows]


def numbers_match(expected: list[Any], actual: list[Any], tolerance: float) -> bool:
    if len(expected) != len(actual):
        return False
    for e, a in zip(expected, actual):
        en, an = _norm(e), _norm(a)
        if isinstance(en, float) and isinstance(an, float):
            if tolerance == 0:
                if int(en) != int(an):
                    return False
            elif abs(en - an) > abs(en) * tolerance:
                return False
        elif en != an:
            return False
    return True


def extract_numbers(text: str) -> list[float]:
    """Numbers in a reply, so a numeric answer can be compared to the truth."""
    out = []
    for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


# ── one case ─────────────────────────────────────────────────────────────────
def run_case(case: Case, ds, tools_schema, tool_map, used_tools: list[str]) -> Result:
    from src.core.agent_loop import run_agent_loop

    trace: dict = {}
    session_id = f"eval_{case.id}_{uuid.uuid4().hex[:8]}"
    started = time.time()
    try:
        run_agent_loop.last_iterations = 0
        reply, script, _, seconds = run_agent_loop(
            case.question,
            session_id,
            tools_schema,
            tool_map,
            db_uri=None,
            trace=trace,
            data_source=ds,
        )
    except Exception as exc:  # a crashed case is a failed case, not a lost run
        return Result(
            case_id=case.id, kind=case.kind, passed=False,
            detail=f"agent raised {type(exc).__name__}: {exc}"[:300],
            seconds=round(time.time() - started, 1),
        )

    # `trace` is the semantic-layer report; the loop's own counters come from
    # the private hook below so the evaluation does not depend on a debug field.
    calls = len(used_tools)
    iterations = int(getattr(run_agent_loop, "last_iterations", 0))
    blocked = int(trace.get("blocked", 0))
    base = dict(
        case_id=case.id, kind=case.kind, seconds=round(seconds, 1),
        iterations=iterations, tool_calls=calls, blocked=blocked,
    )

    if case.kind == "refusal":
        needle = case.expect_error_contains or "Only the gold layer"
        # The guard can fire either as a blocked script or as a tool error the
        # model then reports; both count, but "the reply admits it could not" is
        # not enough — the error text has to be there.
        ok = needle.lower() in (reply or "").lower() or blocked > 0
        return Result(
            passed=ok, detail=f"needle={needle!r} blocked={blocked} calls={calls}", **base
        )

    if case.kind == "tool_used":
        seen = list(used_tools)
        ok = case.expect_tool in seen if case.expect_tool else False
        return Result(
            passed=ok,
            detail=f"expected tool {case.expect_tool!r}; saw {seen}",
            actual=seen, **base,
        )

    # numbers — containment, not equality: a good answer also mentions totals,
    # counts and percentages, so the check is "every expected value appears".
    expected = case.expect or []
    got = extract_numbers(reply)
    missing = [
        e for e in expected
        if not any(
            abs(_norm(e) - a) <= (abs(_norm(e)) * case.tolerance if case.tolerance else 0)
            for a in got
        )
    ]
    ok = not missing
    return Result(
        passed=ok,
        detail=("all expected values present" if ok else f"missing {missing}"),
        expected=expected, actual=got[:20], **base,
    )


# ── report ───────────────────────────────────────────────────────────────────
def summarise(results: list[Result]) -> dict:
    def rate(items: list[Result]) -> float:
        return round(sum(1 for r in items if r.passed) / len(items), 3) if items else 0.0

    numeric = [r for r in results if r.kind == "numbers"]
    refusal = [r for r in results if r.kind == "refusal"]
    tooling = [r for r in results if r.kind == "tool_used"]
    return {
        "cases": len(results),
        "passed": sum(1 for r in results if r.passed),
        "accuracy": rate(numeric),
        "guard_rate": rate(refusal),
        "tool_choice": rate(tooling),
        "avg_iterations": round(sum(r.iterations for r in results) / len(results), 2) if results else 0,
        "avg_seconds": round(sum(r.seconds for r in results) / len(results), 1) if results else 0,
    }


def print_report(summary: dict, results: list[Result]) -> None:
    print("\n" + "=" * 68)
    print("EVALUATION SUMMARY")
    print("=" * 68)
    for key in ("cases", "passed", "accuracy", "guard_rate", "tool_choice",
                "avg_iterations", "avg_seconds"):
        print(f"  {key:<16} {summary[key]}")

    failures = [r for r in results if not r.passed]
    if failures:
        print("\nFAILURES")
        for r in failures:
            print(f"  ✗ {r.case_id} [{r.kind}] {r.detail}")
            if r.expected is not None:
                print(f"      expected={r.expected} actual={r.actual}")
    else:
        print("\nNo failures.")


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(ROOT / "evals" / "marts_core.json"))
    ap.add_argument("--source", default=os.getenv("EVAL_SOURCE", DEFAULT_SOURCE))
    ap.add_argument("--dry-run", action="store_true", help="resolve expectations only, no model calls")
    ap.add_argument("--only", nargs="*", help="run specific case ids")
    ap.add_argument("--max-cases", type=int, default=0, help="cap the number of cases (cost control)")
    ap.add_argument("--min-accuracy", type=float, default=0.8)
    ap.add_argument("--out", default="", help="write results JSON here")
    args = ap.parse_args()

    dataset = json.loads(Path(args.dataset).read_text())
    cases = [Case(**c) for c in dataset["cases"]]
    if args.only:
        cases = [c for c in cases if c.id in set(args.only)]
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        print("No cases selected.")
        return 2

    ds = source_from_spec(args.source, None)
    print(f"source   {args.source}")
    print(f"model    {os.getenv('AGENT_MODEL', 'nemotron-3-super:cloud')}")
    print(f"cases    {len(cases)}")

    # Resolve expectations first: a broken case fails fast, before any spend.
    resolved: dict[str, list[Any]] = {}
    for case in cases:
        if case.kind == "numbers":
            resolved[case.id] = resolve_expectation(case, ds)
            case.expect = resolved[case.id]
    if args.dry_run:
        for case in cases:
            extra = f" expect={case.expect}" if case.kind == "numbers" else ""
            print(f"  {case.id:<22} {case.kind:<10}{extra}")
        print("\ndry run — no model calls made")
        return 0

    from src.core.agent_tools import create_agent_tools

    tools_schema, tool_map = create_agent_tools(source=ds)

    # Tool usage is collected by wrapping the map, so the report can say which
    # tools a case actually used without parsing the transcript.
    used: list[str] = []
    for name, fn in list(tool_map.items()):
        tool_map[name] = _recorder(name, fn, used)

    results: list[Result] = []
    started = time.time()
    for i, case in enumerate(cases, 1):
        used.clear()
        print(f"[{i}/{len(cases)}] {case.id} — {case.question}")
        r = run_case(case, ds, tools_schema, tool_map, used)
        results.append(r)
        # tool_used needs the list the wrapper collected.
        if case.kind == "tool_used":
            pass
        print(f"    {'PASS' if r.passed else 'FAIL'}  {r.iterations} iters, {r.seconds}s  {r.detail}")

    summary = summarise(results)
    print_report(summary, results)

    record = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": os.getenv("AGENT_MODEL", "nemotron-3-super:cloud"),
        "source": args.source,
        "commit": _git_sha(),
        "summary": summary,
        "results": [asdict(r) for r in results],
        "wall_seconds": round(time.time() - started, 1),
    }
    out = Path(args.out) if args.out else ROOT / "evals" / "results" / f"run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nreport  {out}")

    ok = summary["accuracy"] >= args.min_accuracy and summary["guard_rate"] == 1.0
    return 0 if ok else 1


def _recorder(name: str, fn, sink: list[str]):
    def wrapped(*args, **kwargs):
        sink.append(name)
        return fn(*args, **kwargs)

    return wrapped


def _git_sha() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
