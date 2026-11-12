"""BIRD scoring: result-set comparison, SQL extraction from generated Python.

These decide whether a run's number means anything. The comparison follows BIRD's
EX metric — execute both queries, compare result sets as multisets — so a model
that writes a different but equivalent query scores as correct.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import eval_bird as bird  # noqa: E402


def test_same_rows_different_order_match():
    gold = [(1, "a"), (2, "b")]
    pred = [(2, "b"), (1, "a")]
    assert bird.result_matches(gold, pred)


def test_duplicate_rows_are_counted_not_collapsed():
    # A join fan-out returns the same row twice; that is a different answer.
    assert not bird.result_matches([(1,)], [(1,), (1,)])
    assert bird.result_matches([(1,), (1,)], [(1,), (1,)])


def test_int_and_string_number_match():
    # SQLite returns ints here, floats there; "1.0" and 1 are the same answer.
    assert bird.result_matches([(1,)], [("1.0",)])
    assert bird.result_matches([(1.0,)], [(1,)])


def test_float_tolerance():
    assert bird.result_matches([(0.3333333,)], [(0.3333,)])
    assert not bird.result_matches([(0.3,)], [(0.4,)])


def test_case_insensitive_text():
    assert bird.result_matches([("Califonia",)], [("california",)]) is False
    assert bird.result_matches([("Los Angeles",)], [("los angeles",)])


def test_null_matches_null_only():
    assert bird.result_matches([(None,)], [(None,)])
    assert not bird.result_matches([(None,)], [(0,)])


def test_empty_results_match():
    """No rows is a legitimate answer, not an error."""
    assert bird.result_matches([], [])


def test_errors_never_match():
    assert not bird.result_matches(None, [])
    assert not bird.result_matches([], None)


def test_different_column_count_fails():
    assert not bird.result_matches([(1, 2)], [(1,)])


def test_extract_sql_from_ds_run_call():
    script = 'sql = """SELECT a FROM t WHERE x = 1"""\ndf = ds.run(sql, max_rows=100)'
    assert "SELECT a FROM t" in bird.extract_sql(script)


def test_extract_sql_from_inline_string():
    script = 'df = ds.run("SELECT COUNT(*) FROM t", max_rows=10)'
    assert "SELECT COUNT(*)" in bird.extract_sql(script)


def test_extract_sql_prefers_ds_run_over_comment_text():
    script = '# build the query\ndf = ds.run("SELECT name FROM t", max_rows=5)'
    assert bird.extract_sql(script).startswith("SELECT name")


def test_extract_sql_ignores_python_without_sql():
    assert bird.extract_sql("print('hello')") == ""


def test_build_question_includes_evidence_hint():
    row = {"question": "How many?", "evidence": "count means rows"}
    q = bird.build_question(row)
    assert "How many?" in q and "count means rows" in q


def test_build_question_without_evidence():
    assert bird.build_question({"question": "How many?", "evidence": ""}) == "How many?"


def test_classify_missing_sql():
    assert bird.classify(None, None, None, None, "") == "no_sql"


def test_classify_syntax_error():
    assert bird.classify(None, "OperationalError: syntax error near FROM", None, None, "SELECT") == "sql_syntax"


def test_classify_wrong_result():
    assert bird.classify(None, None, [(1,)], [(2,)], "SELECT 1") == "wrong_result"


def test_classify_gold_broken():
    assert bird.classify("no such table", None, [], None, "SELECT 1") == "gold_broken"


def test_linking_summary_survives_agent_error_records():
    summary = bird.summarize_linking([
        {"linked": True, "link_fell_back": False, "full_schema_chars": 200,
         "narrowed_schema_chars": 80},
        {"ok": False, "failure": "error"},
    ])
    assert summary == {
        "used": 1, "fell_back": 0,
        "average_full_schema_chars": 100.0,
        "average_narrowed_schema_chars": 40.0,
    }
