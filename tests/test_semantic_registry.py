"""Tests for the semantic metric registry (R1).

Run: python3 -m pytest tests/test_semantic_registry.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.semantic_registry import (  # noqa: E402
    Metric,
    SemanticRegistry,
    format_violations,
    seed_chinook_demo,
    validate_code,
)


@pytest.fixture()
def reg(tmp_path):
    return SemanticRegistry(str(tmp_path / "registry.db"))


@pytest.fixture()
def seeded(reg):
    seed_chinook_demo(reg)
    return reg


# ---------------------------------------------------------------- CRUD / state

def test_seed_creates_drafts(seeded):
    metrics = seeded.list()
    assert {m.id for m in metrics} == {"revenue", "orders", "customers"}
    assert all(m.status == "draft" for m in metrics)


def test_confirm_records_provenance(seeded):
    m = seeded.confirm("revenue", by="lyken", evidence_query_id="q_001")
    assert m.status == "confirmed"
    assert m.confirmed_by == "lyken"
    assert m.confirmed_at  # ISO timestamp written
    assert m.evidence_query_id == "q_001"


def test_confirm_unknown_raises(reg):
    with pytest.raises(KeyError):
        reg.confirm("nope", by="x")


def test_stale_is_excluded_from_resolution(seeded):
    seeded.confirm("revenue", by="lyken")
    seeded.mark_stale("revenue")
    assert seeded.resolve("今年的营收") == []


def test_upsert_is_idempotent(seeded):
    before = len(seeded.list())
    seed_chinook_demo(seeded)
    assert len(seeded.list()) == before


# ---------------------------------------------------------------- resolution

def test_resolve_chinese_question(seeded):
    seeded.confirm("revenue", by="lyken")
    hits = seeded.resolve("帮我看看今年每个月的营收")
    assert hits and hits[0].id == "revenue"
    assert hits[0].status == "confirmed"


def test_resolve_english_question(seeded):
    seeded.confirm("revenue", by="lyken")
    assert seeded.resolve("what was our total revenue last year")[0].id == "revenue"


def test_resolve_prefers_confirmed_over_higher_draft_score(seeded):
    # "orders" stays draft, "revenue" is confirmed; asking about invoices should
    # surface the confirmed definition first when both match.
    seeded.confirm("revenue", by="lyken")
    hits = seeded.resolve("revenue and number of invoices")
    assert hits[0].status == "confirmed"


def test_resolve_irrelevant_question_returns_nothing(seeded):
    seeded.confirm("revenue", by="lyken")
    assert seeded.resolve("what is the weather in tokyo") == []


# ---------------------------------------------------------------- prompt block

def test_prompt_block_carries_expression_and_provenance(seeded):
    seeded.confirm("revenue", by="lyken", evidence_query_id="q_001")
    block = seeded.prompt_block([seeded.get("revenue")])
    assert "APPROVED METRIC DEFINITIONS" in block
    assert "SUM(Invoice.Total)" in block
    assert "confirmed by lyken" in block
    assert "evidence: q_001" in block
    assert "UnitPrice" in block  # forbidden alternative is surfaced


# ---------------------------------------------------------------- enforcement

def _revenue(seeded):
    seeded.confirm("revenue", by="lyken")
    return [seeded.get("revenue")]


def test_blocks_wrong_definition(seeded):
    metrics = _revenue(seeded)
    code = (
        "df['rev'] = df['UnitPrice'] * df['Quantity']\n"
        "print(df['rev'].sum())\n"
    )
    v = validate_code(code, metrics)
    assert any(x["severity"] == "block" and x["metric"] == "revenue" for x in v)


def test_blocks_wrong_aggregate_on_forbidden_column(seeded):
    metrics = _revenue(seeded)
    v = validate_code("df.groupby('g').agg({'UnitPrice': 'sum'})", metrics)
    assert any(x["severity"] == "block" for x in v)


def test_allows_approved_expression(seeded):
    metrics = _revenue(seeded)
    code = 'q = "SELECT strftime(\'%Y-%m\', InvoiceDate) m, SUM(Invoice.Total) rev FROM Invoice GROUP BY 1"\nprint(q)'
    assert validate_code(code, metrics) == []


def test_allows_approved_expression_after_aliasing(seeded):
    metrics = _revenue(seeded)
    code = "df = pd.read_sql(q, conn)\nprint(df.groupby('m')['Total'].agg('sum'))"
    assert validate_code(code, metrics) == []


def test_comparison_case_is_not_blocked(seeded):
    """Touching a forbidden column is fine when the approved expression is present."""
    metrics = _revenue(seeded)
    code = (
        "line_level = (df['UnitPrice'] * df['Quantity']).sum()\n"
        "invoice_level = df['Total'].sum()   # SUM(Invoice.Total)\n"
        "print(line_level, invoice_level)\n"
    )
    assert validate_code(code, metrics) == []


def test_warns_when_definition_absent_but_nothing_forbidden(seeded):
    metrics = _revenue(seeded)
    v = validate_code("print(df.head())", metrics)
    assert [x["severity"] for x in v] == ["warn"]


def test_drafts_are_not_enforced(seeded):
    # revenue stays draft here
    assert validate_code("df['UnitPrice'].sum()", [seeded.get("revenue")]) == []


def test_format_violations_shows_block_and_warn():
    text = format_violations([
        {"severity": "block", "metric": "revenue", "message": "use SUM"},
        {"severity": "warn", "metric": "orders", "message": "missing"},
    ])
    assert "BLOCKED BY APPROVED METRIC DEFINITIONS" in text
    assert "Definition reminder" in text
