"""
Semantic metric registry.

The registry holds business definitions that the organisation has *confirmed*
("revenue = SUM(Invoice.Total), per invoice, no refunds"), so the agent does not
re-derive them from column names on every session.

Design constraints (see docs/semantic_layer.md):
- SQLite only, no embedding dependency. Resolution is lexical (CJK bigrams +
  latin tokens) so it runs anywhere, including where chromadb is unavailable.
- Definitions are enforced before execution, not merely suggested: a generated
  script that aggregates a forbidden alternative column is blocked.
- Every definition carries provenance: who confirmed it, when, and the query id
  that served as evidence.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

STATUSES = ("draft", "confirmed", "stale")

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "semantic", "registry.db",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    """Lowercase, collapse whitespace, drop backticks/quotes used in SQL."""
    return re.sub(r"\s+", " ", (text or "").replace("`", "").replace('"', "")).strip().lower()


# --------------------------------------------------------------------------- #
# Lexical matching: works without embeddings.
# --------------------------------------------------------------------------- #

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set[str]:
    """Latin words + CJK character bigrams."""
    t = _norm(text)
    toks = set(_WORD.findall(t))
    for run in re.findall(r"[\u4e00-\u9fff]+", t):
        if len(run) == 1:
            toks.add(run)
        for i in range(len(run) - 1):
            toks.add(run[i:i + 2])
    return toks


def _dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

@dataclass
class Metric:
    id: str
    display_name: str
    expr: str
    aliases: list[str] = field(default_factory=list)
    grain: str = ""
    filters: str = ""
    forbidden_columns: list[str] = field(default_factory=list)
    status: str = "draft"
    owner: str = ""
    confirmed_by: str = ""
    confirmed_at: str = ""
    evidence_query_id: str = ""
    source: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "expr": self.expr,
            "aliases": self.aliases,
            "grain": self.grain,
            "filters": self.filters,
            "forbidden_columns": self.forbidden_columns,
            "status": self.status,
            "owner": self.owner,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "evidence_query_id": self.evidence_query_id,
            "source": self.source,
            "score": round(self.score, 4),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id                 TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    expr               TEXT NOT NULL,
    aliases            TEXT NOT NULL DEFAULT '[]',
    grain              TEXT NOT NULL DEFAULT '',
    filters            TEXT NOT NULL DEFAULT '',
    forbidden_columns  TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'draft',
    owner              TEXT NOT NULL DEFAULT '',
    confirmed_by       TEXT NOT NULL DEFAULT '',
    confirmed_at       TEXT NOT NULL DEFAULT '',
    evidence_query_id  TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_status ON metrics(status);
"""


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

# SUM(...), COUNT(DISTINCT ...), AVG(...) etc. One level of nested parens inside.
_AGG_RE = re.compile(
    r"\b(sum|avg|count|min|max)\s*\(\s*(distinct\s+)?([^()]*(?:\([^()]*\)[^()]*)*)\)",
    re.IGNORECASE,
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", _norm(text))


def _aggregates(code: str) -> list[tuple[str, str]]:
    """[(func, normalized-inner-argument), ...]"""
    return [(m.group(1).lower(), _squash(m.group(3))) for m in _AGG_RE.finditer(code or "")]


def _expr_parts(expr: str) -> tuple[str, str]:
    """('sum(invoice.total)') -> ('sum', 'total'), i.e. the column's leaf name."""
    func, _, inner = _squash(expr).partition("(")
    inner = inner.rstrip(")")
    leaf = inner.split(".")[-1]
    return func, leaf


def _uses_approved(code: str, expr: str) -> bool:
    """
    True when the script computes the metric with the approved expression.

    Accepts both the literal form (`SUM(Invoice.Total)`) and the common
    post-query form (`SUM(df['Total'])`) where the column was aliased, because
    the physical column still appears inside the aggregate.
    """
    if not expr:
        return True
    squashed_code = _squash(code)
    if _squash(expr) in squashed_code:
        return True
    func, leaf = _expr_parts(expr)
    if not leaf:
        return False
    for agg_func, inner in _aggregates(code):
        if agg_func == func and _contains_token(inner, leaf):
            return True
    return _pandas_aggregate_of(code, leaf, func)


_PANDAS_AGG_PATTERNS = (
    r"\[\s*['\"]{leaf}['\"]\s*\]\s*\.\s*(?:sum|mean|count|min|max|agg)\s*\(",
    r"\.agg\s*\(\s*\{{\s*['\"]{leaf}['\"]\s*:",
    r"\.agg\s*\(\s*[a-z_]+\s*=\s*\(\s*['\"]{leaf}['\"]\s*,",
)


def _pandas_aggregate_of(code: str, leaf: str, func: str) -> bool:
    """
    Catch the post-query pandas forms that are not written as SQL functions:
    df['Total'].sum(), df.agg({'Total': 'sum'}), df.agg(rev=('Total', 'sum')).
    """
    if func not in ("sum", "mean", "count", "min", "max"):
        return False
    for pattern in _PANDAS_AGG_PATTERNS:
        if re.search(pattern.format(leaf=re.escape(leaf)), code, re.IGNORECASE):
            return True
    return False


def _contains_token(haystack: str, needle: str) -> bool:
    needle = _norm(needle)
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9_.]+", needle):
        return re.search(rf"(?<![a-z0-9_]){re.escape(needle)}(?![a-z0-9_])", haystack) is not None
    return needle in haystack


def validate_code(code: str, metrics: Iterable[Metric]) -> list[dict]:
    """
    Deterministic pre-execution check.

    block : the script computes an active metric from a forbidden alternative
            (e.g. revenue from UnitPrice * Quantity) without also using the
            approved expression. This is the failure the registry exists for.
    warn  : the metric is active but its approved expression never appears —
            surfaced to the model inside the tool result, not blocking.
    """
    normalized = _norm(code)
    violations: list[dict] = []

    for m in metrics:
        if m.status != "confirmed":
            continue
        has_approved = _uses_approved(code, m.expr)

        bad = []
        for col in m.forbidden_columns:
            leaf = _norm(col).split(".")[-1]
            if _contains_token(normalized, leaf):
                bad.append(col)
        if bad and not has_approved:
            violations.append({
                "severity": "block",
                "metric": m.id,
                "message": (
                    f"'{m.display_name}' is a confirmed definition ({m.expr}), but this script uses "
                    f"{', '.join(bad)} instead. Rewrite it with the approved expression."
                ),
            })
            continue

        if not has_approved and m.expr:
            violations.append({
                "severity": "warn",
                "metric": m.id,
                "message": (
                    f"'{m.display_name}' should be computed as {m.expr}"
                    + (f" (grain: {m.grain})" if m.grain else "")
                    + ". The approved expression does not appear in this script."
                ),
            })

    return violations


def format_violations(violations: list[dict]) -> str:
    if not violations:
        return ""
    blocks = [v for v in violations if v["severity"] == "block"]
    warns = [v for v in violations if v["severity"] != "block"]
    out: list[str] = []
    if blocks:
        out.append("⛔ BLOCKED BY APPROVED METRIC DEFINITIONS:")
        out.extend(f"   - {v['message']}" for v in blocks)
    if warns:
        out.append("⚠️ Definition reminder:")
        out.extend(f"   - {v['message']}" for v in warns)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

class SemanticRegistry:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("SEMANTIC_REGISTRY_DB") or _DEFAULT_DB
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._mem_conn = sqlite3.connect(":memory:") if self.path == ":memory:" else None
        self._init()

    # -- plumbing ----------------------------------------------------------- #
    def _conn(self) -> sqlite3.Connection:
        conn = self._mem_conn or sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @staticmethod
    def _row_to_metric(row: sqlite3.Row) -> Metric:
        return Metric(
            id=row["id"],
            display_name=row["display_name"],
            expr=row["expr"],
            aliases=json.loads(row["aliases"] or "[]"),
            grain=row["grain"],
            filters=row["filters"],
            forbidden_columns=json.loads(row["forbidden_columns"] or "[]"),
            status=row["status"],
            owner=row["owner"],
            confirmed_by=row["confirmed_by"],
            confirmed_at=row["confirmed_at"],
            evidence_query_id=row["evidence_query_id"],
            source=row["source"],
        )

    # -- CRUD --------------------------------------------------------------- #
    def upsert(self, metric: Metric) -> Metric:
        if metric.status not in STATUSES:
            raise ValueError(f"unknown status: {metric.status}")
        now = _now()
        with self._conn() as c:
            existing = c.execute("SELECT id FROM metrics WHERE id = ?", (metric.id,)).fetchone()
            if existing:
                c.execute(
                    """UPDATE metrics SET display_name=?, expr=?, aliases=?, grain=?, filters=?,
                           forbidden_columns=?, status=?, owner=?, source=?, updated_at=?
                       WHERE id=?""",
                    (metric.display_name, metric.expr, json.dumps(metric.aliases, ensure_ascii=False),
                     metric.grain, metric.filters, json.dumps(metric.forbidden_columns, ensure_ascii=False),
                     metric.status, metric.owner, metric.source, now, metric.id),
                )
            else:
                c.execute(
                    """INSERT INTO metrics (id, display_name, expr, aliases, grain, filters,
                           forbidden_columns, status, owner, source, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (metric.id, metric.display_name, metric.expr,
                     json.dumps(metric.aliases, ensure_ascii=False), metric.grain, metric.filters,
                     json.dumps(metric.forbidden_columns, ensure_ascii=False), metric.status,
                     metric.owner, metric.source, now, now),
                )
        return self.get(metric.id)

    def get(self, metric_id: str) -> Optional[Metric]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM metrics WHERE id = ?", (metric_id,)).fetchone()
        return self._row_to_metric(row) if row else None

    def list(self, status: Optional[str] = None, source: Optional[str] = None) -> list[Metric]:
        q = "SELECT * FROM metrics WHERE 1=1"
        args: list[str] = []
        if status:
            q += " AND status = ?"
            args.append(status)
        if source:
            q += " AND (source = '' OR source = ?)"
            args.append(source)
        q += " ORDER BY status, id"
        with self._conn() as c:
            return [self._row_to_metric(r) for r in c.execute(q, args)]

    def confirm(self, metric_id: str, by: str, evidence_query_id: str = "") -> Metric:
        with self._conn() as c:
            cur = c.execute(
                """UPDATE metrics SET status='confirmed', confirmed_by=?, confirmed_at=?,
                       evidence_query_id=?, updated_at=? WHERE id=?""",
                (by, _now(), evidence_query_id, _now(), metric_id),
            )
            if cur.rowcount == 0:
                raise KeyError(metric_id)
        return self.get(metric_id)

    def mark_stale(self, metric_id: str) -> Metric:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE metrics SET status='stale', updated_at=? WHERE id=?", (_now(), metric_id)
            )
            if cur.rowcount == 0:
                raise KeyError(metric_id)
        return self.get(metric_id)

    def delete(self, metric_id: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM metrics WHERE id = ?", (metric_id,)).rowcount > 0

    # -- resolution --------------------------------------------------------- #
    def resolve(
        self,
        question: str,
        source: Optional[str] = None,
        limit: int = 3,
        min_score: float = 0.30,
    ) -> list[Metric]:
        """Return the confirmed (then draft) metrics that best match the question."""
        q_tokens = _tokens(question)
        if not q_tokens:
            return []
        q_norm = _norm(question)

        scored: list[tuple[float, Metric]] = []
        for m in self.list(source=source):
            if m.status == "stale":
                continue
            best = _dice(q_tokens, _tokens(m.display_name))
            for alias in m.aliases:
                a_norm = _norm(alias)
                score = _dice(q_tokens, _tokens(alias))
                # A phrase that literally appears in the question is a strong hit.
                if a_norm and a_norm in q_norm:
                    score = max(score, 0.95)
                best = max(best, score)
            if best >= min_score:
                m.score = best
                scored.append((best, m))

        # confirmed first, then score
        scored.sort(key=lambda pair: (pair[1].status != "confirmed", -pair[0], pair[1].id))
        return [m for _, m in scored[:limit]]

    # -- prompt surface ----------------------------------------------------- #
    def prompt_block(self, metrics: list[Metric]) -> str:
        if not metrics:
            return ""
        lines = [
            "",
            "## APPROVED METRIC DEFINITIONS — AUTHORITATIVE",
            "These definitions were confirmed by the business. Use them verbatim; do not invent",
            "an alternative formula for the same concept. If a definition looks wrong, say so in",
            "your final answer instead of silently using a different one.",
        ]
        for m in metrics:
            head = f"- {m.display_name} ({m.id}) = {m.expr}"
            meta = []
            if m.grain:
                meta.append(f"grain: {m.grain}")
            if m.filters:
                meta.append(f"filters: {m.filters}")
            if m.confirmed_by:
                meta.append(f"confirmed by {m.confirmed_by} on {(m.confirmed_at or '')[:10]}")
            if m.evidence_query_id:
                meta.append(f"evidence: {m.evidence_query_id}")
            lines.append(head)
            if meta:
                lines.append("  " + " | ".join(meta))
            if m.forbidden_columns:
                lines.append(f"  forbidden alternatives: {', '.join(m.forbidden_columns)}")
        lines.append("")
        return "\n".join(lines)

    # -- enforcement -------------------------------------------------------- #
    def validate(self, code: str, metrics: list[Metric]) -> list[dict]:
        return validate_code(code, metrics)


def seed_chinook_demo(registry: SemanticRegistry) -> list[Metric]:
    """Definitions for the bundled Chinook database, as drafts."""
    demo = [
        Metric(
            id="revenue",
            display_name="营收",
            expr="SUM(Invoice.Total)",
            aliases=["营收", "销售额", "营业收入", "卖了多少钱", "总收入", "revenue", "total revenue", "sales"],
            grain="per invoice",
            filters="none (no refunds, no tax adjustments)",
            forbidden_columns=["InvoiceLine.UnitPrice", "UnitPrice", "Quantity"],
            owner="finance",
            source="chinook",
        ),
        Metric(
            id="orders",
            display_name="订单数",
            expr="COUNT(DISTINCT Invoice.InvoiceId)",
            aliases=["订单数", "发票数", "订单量", "order count", "number of invoices", "how many orders"],
            grain="per invoice",
            forbidden_columns=[],
            owner="finance",
            source="chinook",
        ),
        Metric(
            id="customers",
            display_name="付费客户数",
            expr="COUNT(DISTINCT Invoice.CustomerId)",
            aliases=["客户数", "付费客户", "有多少客户", "customer count", "paying customers"],
            grain="per customer",
            forbidden_columns=[],
            owner="finance",
            source="chinook",
        ),
    ]
    for m in demo:
        registry.upsert(m)
    return demo
