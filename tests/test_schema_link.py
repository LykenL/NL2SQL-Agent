"""Schema linking and stratified sampling.

These decide whether a benchmark run measures the model or the harness: an
unstratified sample measures one database, and a broken shortlist silently makes
every answer impossible.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import eval_bird as bird  # noqa: E402
from src.core import schema_link  # noqa: E402

SCHEMA = {
    "schools": {"columns": [
        {"name": "CDSCode", "type": "TEXT"},
        {"name": "School", "type": "TEXT"},
        {"name": "County", "type": "TEXT"},
    ]},
    "frpm": {"columns": [
        {"name": "CDSCode", "type": "TEXT"},
        {"name": "Enrollment (K-12)", "type": "INTEGER"},
    ]},
    "satscores": {"columns": [{"name": "cds", "type": "TEXT"}]},
}


def _schema_json():
    return json.dumps(SCHEMA)


# -- parsing ------------------------------------------------------------------
def test_parse_plain_json():
    got = schema_link._parse_json('{"tables": ["a"], "columns": {}}')
    assert got["tables"] == ["a"]


def test_parse_json_inside_fence():
    got = schema_link._parse_json('Here you go:\n```json\n{"tables": ["a"]}\n```\n')
    assert got["tables"] == ["a"]


def test_parse_json_with_prose_around_it():
    got = schema_link._parse_json('I think {"tables": ["a", "b"]} is right.')
    assert got["tables"] == ["a", "b"]


def test_parse_garbage_returns_empty():
    assert schema_link._parse_json("no json here") == {}
    assert schema_link._parse_json("") == {}
    assert schema_link._parse_json('{"tables": ') == {}


# -- narrowing ----------------------------------------------------------------
def test_apply_links_keeps_only_requested_tables():
    narrowed = json.loads(schema_link.apply_links(
        _schema_json(), {"tables": ["schools"], "columns": {}}))
    assert set(narrowed) == {"schools"}


def test_apply_links_filters_columns():
    narrowed = json.loads(schema_link.apply_links(
        _schema_json(), {"tables": ["schools"], "columns": {"schools": ["School"]}}))
    names = [c["name"] for c in narrowed["schools"]["columns"]]
    assert names == ["School"]


def test_empty_column_list_keeps_table_intact():
    """An empty shortlist of columns must not hide the table's columns."""
    narrowed = json.loads(schema_link.apply_links(
        _schema_json(), {"tables": ["schools"], "columns": {"schools": []}}))
    assert len(narrowed["schools"]["columns"]) == 3


def test_unknown_column_names_fall_back_to_all_columns():
    narrowed = json.loads(schema_link.apply_links(
        _schema_json(), {"tables": ["schools"], "columns": {"schools": ["Nope"]}}))
    assert len(narrowed["schools"]["columns"]) == 3


def test_unknown_tables_are_reported_not_silently_dropped():
    narrowed = json.loads(schema_link.apply_links(
        _schema_json(), {"tables": ["schools", "ghost_table"], "columns": {}}))
    assert "schools" in narrowed
    assert "ghost_table" not in narrowed
    assert "ghost_table" in narrowed["_note"]


def test_all_tables_unknown_returns_original_schema():
    """A fully wrong shortlist must not produce an empty schema."""
    out = schema_link.apply_links(_schema_json(), {"tables": ["ghost"], "columns": {}})
    assert set(json.loads(out)) == {"schools", "frpm", "satscores"}


def test_verify_links_keeps_valid_tables_and_reports_dropped():
    """One hallucinated name must not discard the whole shortlist."""
    got = schema_link.verify_links(_schema_json(), {"tables": ["schools", "ghost"]})
    assert got["ok"] is True
    assert got["tables"] == ["schools"]
    assert got["dropped"] == ["ghost"]


def test_verify_links_fails_only_when_nothing_is_valid():
    got = schema_link.verify_links(_schema_json(), {"tables": ["ghost", "phantom"]})
    assert got["ok"] is False
    assert got["tables"] == []
    assert got["dropped"] == ["ghost", "phantom"]


def test_verify_links_on_invalid_schema():
    got = schema_link.verify_links("not json", {"tables": ["a"]})
    assert got["ok"] is False


def test_enabled_env_flag(monkeypatch):
    monkeypatch.delenv("SCHEMA_LINKING", raising=False)
    assert schema_link.enabled() is False
    monkeypatch.setenv("SCHEMA_LINKING", "on")
    assert schema_link.enabled() is True


# -- stratified sampling ------------------------------------------------------
class FakeDS(list):
    """Stands in for a HuggingFace Dataset: iterable of row dicts."""


def _fake_dataset(counts):
    rows = []
    qid = 0
    for db, n in counts.items():
        for _ in range(n):
            rows.append({"id": qid, "db": db, "question": "q",
                         "evidence": "", "SQL": "SELECT 1", "difficulty": "challenging"})
            qid += 1
    return rows


def test_stratify_covers_every_database():
    counts = {"big": 400, "mid": 80, "small": 15, "tiny": 5}
    rows = _fake_dataset(counts)
    got = bird.stratify_rows(rows, 50)
    dbs = {r["db"] for r in got}
    assert dbs == set(counts), "every database must be represented"


def test_stratify_is_roughly_proportional():
    counts = {"big": 400, "mid": 80, "small": 20}
    rows = _fake_dataset(counts)
    got = bird.stratify_rows(rows, 50)
    n = {db: sum(1 for r in got if r["db"] == db) for db in counts}
    assert n["big"] > n["mid"] > n["small"]
    assert abs(sum(n.values()) - 50) <= 1


def test_stratify_respects_limit():
    rows = _fake_dataset({"a": 100, "b": 100})
    assert len(bird.stratify_rows(rows, 10)) <= 11


def test_stratify_never_exceeds_available():
    rows = _fake_dataset({"a": 3, "b": 2})
    got = bird.stratify_rows(rows, 50)
    assert len(got) == 5


def test_cohort_manifests_are_disjoint_and_keep_historical_sample():
    rows = _fake_dataset({"a": 200, "b": 200, "c": 100})
    manifests = bird.build_cohort_manifests(rows)

    historical = manifests["historical"]["question_ids"]
    development = manifests["development"]["question_ids"]
    blind = manifests["blind"]["question_ids"]
    assert historical == [r["id"] for r in bird.stratify_rows(rows, 50)]
    assert len(historical) == 50
    assert len(development) == 100
    assert len(blind) == 50
    assert not (set(historical) & set(development))
    assert not (set(historical) & set(blind))
    assert not (set(development) & set(blind))


def test_rows_for_ids_preserves_manifest_order():
    rows = _fake_dataset({"a": 3})
    selected = bird._rows_for_ids(rows, [2, 0])
    assert [r["id"] for r in selected] == [2, 0]


def test_inspection_query_rejected_by_submit():
    """A bare column dump is never the answer; the agent runs out of turns and
    submits one, which is how a thrombosis question produced 1238 rows."""
    import src.core.agent_tools as at

    assert at._LOOKS_LIKE_INSPECTION.match("SELECT ID, Diagnosis FROM Patient")
    assert at._LOOKS_LIKE_INSPECTION.match("select * from t")
    # Real answers are not inspection queries.
    assert not at._LOOKS_LIKE_INSPECTION.match(
        "SELECT COUNT(*) FROM Patient WHERE Diagnosis LIKE '%RA%'")
    assert not at._LOOKS_LIKE_INSPECTION.match(
        "SELECT ID FROM Patient WHERE PLT > 100")
    assert not at._LOOKS_LIKE_INSPECTION.match("SELECT 1")
