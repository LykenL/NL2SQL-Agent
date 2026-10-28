#!/usr/bin/env python3
"""
Admin CLI for the semantic metric registry.

    python scripts/semantic_admin.py seed-chinook
    python scripts/semantic_admin.py list [--status draft|confirmed|stale]
    python scripts/semantic_admin.py resolve "今年的营收是多少"
    python scripts/semantic_admin.py confirm revenue --by lyken --evidence q_001
    python scripts/semantic_admin.py add --id revenue --name 营收 --expr "SUM(Invoice.Total)" \
        --alias 营收 --alias revenue --forbid UnitPrice
    python scripts/semantic_admin.py stale revenue
    python scripts/semantic_admin.py delete revenue
    python scripts/semantic_admin.py check <script.py> --metric revenue
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.semantic_registry import (  # noqa: E402
    Metric,
    SemanticRegistry,
    format_violations,
    seed_chinook_demo,
    validate_code,
)


def _print(metrics):
    if not metrics:
        print("(no metrics)")
        return
    for m in metrics:
        score = f"  score={m.score:.2f}" if m.score else ""
        print(f"{m.status:<9} {m.id:<12} {m.display_name:<10} = {m.expr}{score}")
        if m.confirmed_by:
            print(f"          confirmed by {m.confirmed_by} at {m.confirmed_at} (evidence: {m.evidence_query_id or '-'})")
        if m.forbidden_columns:
            print(f"          forbidden: {', '.join(m.forbidden_columns)}")


def main():
    ap = argparse.ArgumentParser(description="Semantic metric registry admin")
    ap.add_argument("--db", help="registry sqlite path (default: data/semantic/registry.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed-chinook", help="insert the bundled Chinook definitions as drafts")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=["draft", "confirmed", "stale"])

    p_res = sub.add_parser("resolve")
    p_res.add_argument("question")
    p_res.add_argument("--limit", type=int, default=3)

    p_conf = sub.add_parser("confirm")
    p_conf.add_argument("metric_id")
    p_conf.add_argument("--by", required=True)
    p_conf.add_argument("--evidence", default="")

    p_stale = sub.add_parser("stale")
    p_stale.add_argument("metric_id")

    p_del = sub.add_parser("delete")
    p_del.add_argument("metric_id")

    p_add = sub.add_parser("add")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--expr", required=True)
    p_add.add_argument("--alias", action="append", default=[])
    p_add.add_argument("--grain", default="")
    p_add.add_argument("--filters", default="")
    p_add.add_argument("--forbid", action="append", default=[])
    p_add.add_argument("--owner", default="")
    p_add.add_argument("--source", default="")

    p_check = sub.add_parser("check", help="validate a script against confirmed metrics")
    p_check.add_argument("script")
    p_check.add_argument("--metric", action="append", default=[], help="metric id to enforce")

    args = ap.parse_args()

    registry = SemanticRegistry(args.db) if args.db else SemanticRegistry()

    if args.cmd == "seed-chinook":
        _print(seed_chinook_demo(registry))
    elif args.cmd == "list":
        _print(registry.list(status=args.status))
    elif args.cmd == "resolve":
        _print(registry.resolve(args.question, limit=args.limit))
    elif args.cmd == "confirm":
        _print([registry.confirm(args.metric_id, by=args.by, evidence_query_id=args.evidence)])
    elif args.cmd == "stale":
        _print([registry.mark_stale(args.metric_id)])
    elif args.cmd == "delete":
        print("deleted" if registry.delete(args.metric_id) else "not found")
    elif args.cmd == "add":
        _print([registry.upsert(Metric(
            id=args.id, display_name=args.name, expr=args.expr, aliases=args.alias,
            grain=args.grain, filters=args.filters, forbidden_columns=args.forbid,
            owner=args.owner, source=args.source,
        ))])
    elif args.cmd == "check":
        metrics = [registry.get(mid) for mid in args.metric] if args.metric else registry.list(status="confirmed")
        metrics = [m for m in metrics if m]
        code = open(args.script).read()
        violations = validate_code(code, metrics)
        if not violations:
            print("OK: no violations")
        else:
            print(format_violations(violations))
            print()
            print(json.dumps(violations, indent=2, ensure_ascii=False))
            sys.exit(1)


if __name__ == "__main__":
    main()
