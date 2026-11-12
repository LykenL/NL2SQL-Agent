"""LLM schema linking: pick the tables and columns a question needs.

Why: dumping a whole database into the prompt (european_football_2 has 201
columns) makes the model choose among dozens of tables and hundreds of columns,
which is where "picked the right table, wrong column" failures come from. One
cheap call that returns only names shrinks the candidate set before any SQL is
written, and cuts the per-call input cost at the same time.

Design constraints learned from the benchmark:
  - A wrong shortlist is worse than no shortlist, so the caller must be able to
    fall back to the full schema and the run record must say when it did.
  - The answer is names only (tables, and optionally columns). Anything else the
    model returns is ignored.
  - With no model key or in product mode this module is never called.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

LINK_PROMPT = """You choose which database objects a SQL question needs.

Database schema (JSON: table -> columns):
{schema}

Question:
{question}

Return ONLY JSON, no prose:
{{"tables": ["table_a", "table_b"], "columns": {{"table_a": ["col1", "col2"]}}}}

Rules:
- Include every table that is plausibly needed, including tables used only for a
  join key. Missing a needed table makes the answer impossible.
- For each chosen table, list the columns the question needs or that are useful
  for filtering/joining. If unsure about a table, still include it with its keys.
- Use the exact names from the schema. Do not invent names, do not rename.
"""


def _parse_json(text: str) -> dict:
    """Models wrap JSON in prose or fences; take the outermost object."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def select_links(schema_json: str, question: str, model: str = "",
                 max_tables: int = 6, timeout_calls: int = 1) -> dict:
    """One call to the chat model; returns {"tables": [...], "columns": {...}}.

    Returns {} on any failure — the caller treats that as "no shortlist" and uses
    the full schema.
    """
    from src.core import tracing

    try:
        client = tracing.openai_client()
    except Exception:
        return {}

    prompt = LINK_PROMPT.format(schema=schema_json[:24000], question=question)
    kwargs: dict[str, Any] = {
        "model": model or tracing.llm_model(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        # The output is a small JSON object, but thinking tokens are drawn from the
        # same budget: with thinking on, 600 tokens were spent entirely on
        # reasoning, finish_reason came back as "length" and content was empty —
        # which silently looked like "no shortlist" on two thirds of the runs.
        "max_tokens": 1200,
        "extra_body": tracing.extra_body(tracing.linker_thinking_enabled()),
    }
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # network, auth, quota: fall back, never fail the run
        print(f"[schema-link] skipped: {type(exc).__name__}: {exc}")
        return {}

    finish = resp.choices[0].finish_reason
    raw = resp.choices[0].message.content or ""
    if finish == "length":
        # Truncated output is not an answer. Say so instead of returning {} and
        # letting the caller believe the model chose nothing.
        print(f"[schema-link] truncated (finish=length, {len(raw)} chars) — using full schema")
        return {}
    parsed = _parse_json(raw)
    tables = [t for t in parsed.get("tables", []) if isinstance(t, str)]
    columns = parsed.get("columns") if isinstance(parsed.get("columns"), dict) else {}

    # Trim to the budget while keeping the order the model gave.
    tables = tables[:max_tables]
    columns = {t: cols for t, cols in columns.items() if t in tables}

    if not tables:
        return {}
    return {"tables": tables, "columns": columns, "raw_chars": len(raw)}


def apply_links(schema_json: str, links: dict) -> str:
    """Return schema_json restricted to the linked tables/columns (best effort)."""
    try:
        full = json.loads(schema_json)
    except json.JSONDecodeError:
        return schema_json
    if not links.get("tables"):
        return schema_json

    out: dict[str, Any] = {}
    for table in links["tables"]:
        if table not in full:
            continue
        entry = dict(full[table])
        wanted = links.get("columns", {}).get(table) or []
        if wanted and entry.get("columns"):
            keep = [c for c in entry["columns"] if c.get("name") in wanted]
            # Never drop everything: if the model named columns that do not exist,
            # keep the table intact rather than hiding it behind an empty list.
            entry["columns"] = keep or entry["columns"]
        out[table] = entry

    if not out:
        return schema_json
    missing = [t for t in links["tables"] if t not in full]
    if missing:
        # Show the model what it got wrong instead of silently narrowing.
        out["_note"] = f"tables not found in schema: {missing}"
    return json.dumps(out, indent=2)


def verify_links(schema_json: str, links: dict) -> dict:
    """Check a shortlist against the real schema and repair it.

    Requiring every linked table to exist was too strict: one hallucinated table
    name threw away a perfectly good shortlist and fell back to the full schema
    on two thirds of the benchmark. The repair rule is:
      - keep the tables that exist, drop the ones that do not (and say so)
      - if nothing valid remains, report it so the caller uses the full schema
    Returns {"tables": [...], "dropped": [...], "ok": bool}.
    """
    try:
        full = json.loads(schema_json)
    except json.JSONDecodeError:
        return {"tables": [], "dropped": list(links.get("tables", [])), "ok": False}

    wanted = [t for t in links.get("tables", []) if isinstance(t, str)]
    kept = [t for t in wanted if t in full]
    dropped = [t for t in wanted if t not in full]
    return {"tables": kept, "dropped": dropped, "ok": bool(kept)}


def expand_fk_neighbors(schema_json: str, links: dict) -> dict:
    """Add every one-hop foreign-key neighbour of the linked tables.

    The LLM is good at identifying a fact table but can omit the directly joined
    dimension table.  A shortlist must therefore be recall-oriented: an FK
    neighbour is cheap context and omitting it makes otherwise valid joins
    impossible.  Both outgoing and incoming edges matter in SQLite schemas.
    """
    try:
        full = json.loads(schema_json)
    except json.JSONDecodeError:
        return {"tables": [], "added_tables": []}

    selected = [t for t in links.get("tables", []) if t in full]
    selected_set = set(selected)
    neighbours: set[str] = set()
    for table, entry in full.items():
        for fk in entry.get("foreign_keys", []) or []:
            target = fk.get("to_table")
            if table in selected_set and target in full:
                neighbours.add(target)
            if target in selected_set:
                neighbours.add(table)

    # Preserve the model's relevance ordering, then the database's stable
    # schema order for neighbours, so the prompt is deterministic.
    added = [table for table in full if table in neighbours and table not in selected_set]
    return {"tables": selected + added, "added_tables": added}


def enabled() -> bool:
    return os.getenv("SCHEMA_LINKING", "off").lower() in {"1", "on", "true", "yes"}
