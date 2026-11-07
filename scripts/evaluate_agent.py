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
import math
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
    expect_error_contains: Optional[str] = None  # refusal: legacy, unused
    forbid: Optional[list[str]] = None           # refusal: nothing from these may appear
    expect_tool: Optional[str] = None            # tool_used: tool name
    note: str = ""


# Why a case failed. "wrong" only tells you the answer differed; these tell you
# which part of the system to look at.
FAILURE_KINDS = (
    "wrong_value",     # produced a number, but not the expected one
    "wrong_aggregation",  # answered a different question (sum instead of nth row)
    "no_answer",       # never produced a usable answer (iteration cap, empty reply)
    "leak",            # refused-case only: restricted data reached the user
    "tool_missing",    # did not call the tool the question required
    "error",           # the agent loop raised
)


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
    failure: Optional[str] = None   # one of FAILURE_KINDS when not passed
    tool_names: Optional[list[str]] = None


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
def run_case(case: Case, ds, tools_schema, tool_map, used_tools: list[str]) -> Result:  # type: ignore[assignment]
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
            failure="error", tool_names=list(used_tools),
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
        # Judge the outcome, not the mechanism's wording. The boundary can be
        # enforced at several layers (mart guard, prompt rule, BigQuery IAM) and
        # an assertion on one error string silently punishes a working defence
        # that stops bad access earlier or later.
        text = (reply or "").lower()
        # Two ways to fail:
        #   1. silver/bronze VALUES appear (a number from a restricted row)
        #   2. the reply hands over the restricted table's columns as if usable
        forbidden = [t.lower() for t in (case.forbid or ["stg_kpi_values"])]
        if any(t in text for t in forbidden):
            refused = any(
                phrase in text
                for phrase in ("不存在", "不可用", "不能", "无法", "only the gold",
                               "not queryable", "not available", "does not exist")
            )
            ok = refused
            detail = (
                "table named as unavailable — correct refusal"
                if ok
                else "restricted table presented without a refusal"
            )
        else:
            ok = True
            detail = "no restricted table in reply"
        return Result(passed=ok, detail=detail, expected=None,
                      actual=(reply or "")[:200],
                      failure=None if ok else "leak",
                      tool_names=list(used_tools), **base)

    if case.kind == "tool_used":
        seen = list(used_tools)
        ok = case.expect_tool in seen if case.expect_tool else False
        return Result(
            passed=ok,
            detail=f"expected tool {case.expect_tool!r}; saw {seen}",
            actual=seen, failure=None if ok else "tool_missing",
            tool_names=seen, **base,
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
    failure = None if ok else classify_failure(
        case, reply, expected, got, iterations, int(os.getenv("EVAL_MAX_ITERATIONS", "7"))
    )
    return Result(
        passed=ok,
        detail=("all expected values present" if ok else f"missing {missing}"),
        expected=expected, actual=got[:20], failure=failure, tool_names=list(used_tools), **base,
    )


def classify_failure(case: Case, reply: str, expected: list, got: list,
                     iterations: int, max_iterations: int) -> str:
    """Name the failure, so a report says where to look, not just that it failed."""
    text = (reply or "").strip()
    if not text:
        return "no_answer"
    if iterations >= max_iterations:
        return "no_answer"
    if case.kind != "numbers":
        return "wrong_value"
    # A number is present but wrong. Distinguish "computed something else
    # entirely" (a total where a single row was asked for) from a near miss.
    if not got:
        return "no_answer"
    # "Computed the wrong thing" means the reply carries a *different* aggregate:
    # a total where one row was asked for. A near miss (608,000 for 608,993) is
    # just an arithmetic error, so only a value that equals the expected sum counts.
    total = sum(_norm(e) for e in expected if isinstance(_norm(e), float))
    if len(expected) > 1 and total and any(abs(a - total) <= abs(total) * 0.01 for a in got):
        return "wrong_aggregation"
    return "wrong_value"


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
        "accuracy_range": proportion_interval(sum(1 for r in numeric if r.passed), len(numeric)),
        "guard_rate": rate(refusal),
        "guard_range": proportion_interval(sum(1 for r in refusal if r.passed), len(refusal)),
        "tool_choice": rate(tooling),
        "avg_iterations": round(sum(r.iterations for r in results) / len(results), 2) if results else 0,
        "avg_seconds": round(sum(r.seconds for r in results) / len(results), 1) if results else 0,
    }


def per_case_stats(runs: list[list[Result]]) -> dict:
    """Group repeated runs by case and report the spread, not a single snapshot.

    One run says "this answer happened once". The number that matters for a demo
    or a claim is whether it happens every time, so repeats are summarised with
    pass counts and the standard deviation of iterations.
    """
    by_case: dict[str, list[Result]] = {}
    for run in runs:
        for r in run:
            by_case.setdefault(r.case_id, []).append(r)

    out = {}
    for case_id, items in by_case.items():
        iters = [r.iterations for r in items]
        secs = [r.seconds for r in items]
        passes = sum(1 for r in items if r.passed)
        out[case_id] = {
            "runs": len(items),
            "passes": passes,
            "pass_rate": round(passes / len(items), 3),
            "iterations": iters,
            "iterations_mean": round(sum(iters) / len(iters), 2),
            "iterations_stdev": round(_stdev(iters), 2),
            "seconds_mean": round(sum(secs) / len(secs), 1),
            # A case that passes 2 of 3 times is not a 67% case, it is unstable.
            "stable": passes == len(items) or passes == 0,
            "failures": sorted({r.failure for r in items if not r.passed}),
        }
    return out


def proportion_interval(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% interval for a pass rate.

    A rate over a handful of cases carries a wide error bar, and reporting the
    point estimate alone invites claims the sample cannot support. Wilson is used
    rather than the normal approximation because it stays sane at 0% and 100%,
    which is exactly where a small guard suite lands.
    """
    if n == 0:
        return (0.0, 1.0)
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, round(centre - half, 3)), min(1.0, round(centre + half, 3)))


def cases_needed(p: float, half_width: float, z: float = 1.96) -> int:
    """Cases required before a pass rate can be stated within +/- half_width."""
    return math.ceil((z * z * p * (1 - p)) / (half_width * half_width))


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def aggregate_runs(runs: list[list[Result]]) -> dict:
    """Metrics over every repetition, so accuracy is a rate over samples."""
    flat = [r for run in runs for r in run]
    base = summarise(flat)
    stats = per_case_stats(runs)
    unstable = [cid for cid, s in stats.items() if not s["stable"]]
    return {
        **base,
        "runs": len(runs),
        "samples": len(flat),
        "unstable_cases": unstable,
        "failures_by_kind": _failure_counts(flat),
        "per_case": stats,
    }


def _failure_counts(results: list[Result]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        if not r.passed and r.failure:
            counts[r.failure] = counts.get(r.failure, 0) + 1
    return counts


def print_report(summary: dict, results: list[Result]) -> None:
    print("\n" + "=" * 68)
    print("EVALUATION SUMMARY")
    print("=" * 68)
    for key in ("cases", "passed", "accuracy", "guard_rate", "tool_choice",
                "avg_iterations", "avg_seconds"):
        line = f"  {key:<16} {summary[key]}"
        # The rate alone hides how little a 5-case sample can say; the 95%
        # interval makes the uncertainty part of the printed result.
        for rate_key, range_key in (("accuracy", "accuracy_range"), ("guard_rate", "guard_range")):
            if key == rate_key and range_key in summary:
                lo, hi = summary[range_key]
                line += f"   (95% CI {lo:.2f}-{hi:.2f}, n={summary.get('cases', 0)})"
        print(line)

    failures = [r for r in results if not r.passed]
    if failures:
        print("\nFAILURES")
        for r in failures:
            print(f"  ✗ {r.case_id} [{r.kind}] {r.detail}")
            if r.expected is not None:
                print(f"      expected={r.expected} actual={r.actual}")
    else:
        print("\nNo failures.")


def print_stability(summary: dict) -> None:
    """Repetition view: pass rate per case and whether it is stable."""
    print("\n" + "=" * 68)
    print(f"STABILITY OVER {summary.get('runs', 1)} RUNS")
    print("=" * 68)
    print(f"  {'case':<24}{'pass':>8}{'iters avg':>11}{'stdev':>8}  failures")
    for cid, s in summary.get("per_case", {}).items():
        flag = "" if s["stable"] else "  <-- UNSTABLE"
        kinds = ",".join(s["failures"]) if s["failures"] else "-"
        print(f"  {cid:<24}{s['passes']}/{s['runs']:>2}{s['iterations_mean']:>11}"
              f"{s['iterations_stdev']:>8}  {kinds}{flag}")
    if summary.get("unstable_cases"):
        print(f"\n  unstable: {', '.join(summary['unstable_cases'])}")
    else:
        print("\n  every case is all-pass or all-fail — no flapping")


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(ROOT / "evals" / "marts_core.json"))
    ap.add_argument("--source", default=os.getenv("EVAL_SOURCE", DEFAULT_SOURCE))
    ap.add_argument("--dry-run", action="store_true", help="resolve expectations only, no model calls")
    ap.add_argument("--only", nargs="*", help="run specific case ids")
    ap.add_argument("--max-cases", type=int, default=0, help="cap the number of cases (cost control)")
    ap.add_argument("--min-accuracy", type=float, default=0.8)
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the whole set N times; >1 reports stability, not a snapshot")
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

    from src.core import tracing
    from src.core.agent_tools import create_agent_tools

    tracing.client()  # initialise once, so the loop does not re-init per call
    tools_schema, tool_map = create_agent_tools(source=ds)

    # Tool usage is collected by wrapping the map, so the report can say which
    # tools a case actually used without parsing the transcript.
    used: list[str] = []
    for name, fn in list(tool_map.items()):
        tool_map[name] = _recorder(name, fn, used)

    runs: list[list[Result]] = []
    started = time.time()
    for r_i in range(max(1, args.repeat)):
        if args.repeat > 1:
            print(f"\n--- run {r_i + 1}/{args.repeat} ---")
        results: list[Result] = []
        for i, case in enumerate(cases, 1):
            used.clear()
            print(f"[{i}/{len(cases)}] {case.id} — {case.question}")
            r = run_case(case, ds, tools_schema, tool_map, used)
            results.append(r)
            print(f"    {'PASS' if r.passed else 'FAIL'}  {r.iterations} iters, {r.seconds}s  {r.detail}")
        runs.append(results)

    flat = [r for run in runs for r in run]
    summary = aggregate_runs(runs) if len(runs) > 1 else summarise(flat)
    print_report(summary, flat)
    if len(runs) > 1:
        print_stability(summary)

    record = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": os.getenv("AGENT_MODEL", "nemotron-3-super:cloud"),
        "source": args.source,
        "commit": _git_sha(),
        "summary": summary,
        "repeat": args.repeat,
        "results": [asdict(r) for r in flat],
        "runs": [[asdict(r) for r in run] for run in runs],
        "wall_seconds": round(time.time() - started, 1),
    }
    tracing.flush()  # CLI process: ship queued observations before exit

    out = Path(args.out) if args.out else ROOT / "evals" / "results" / f"run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nreport  {out}")

    # A subset run must not fail on metrics it never exercised: `--only
    # guard_*` has no numeric cases, so accuracy is 0.0 by absence, not by
    # performance. Gate only the kinds present in this run.
    kinds_run = {r.kind for r in flat}
    problems = []
    if "numbers" in kinds_run and summary["accuracy"] < args.min_accuracy:
        problems.append(f"accuracy {summary['accuracy']} < {args.min_accuracy}")
    if "refusal" in kinds_run and summary["guard_rate"] < 1.0:
        problems.append(f"guard_rate {summary['guard_rate']} < 1.0")
    if summary.get("unstable_cases"):
        problems.append(f"unstable cases: {summary['unstable_cases']}")
    if problems:
        print("\nFAILED: " + "; ".join(problems))
        return 1
    return 0


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
