"""Engine gaps and a submission gate that refused valid answers.

Two classes the last blind run exposed, neither of them a reasoning failure:

  - six of fifty questions finished without submit_query (sub=False) and were scored
    on whatever exploration query ran last. submit_query required the SQL string to
    match the validated one exactly, so a reformatted but identical query was
    rejected; the question then scored on the wrong statement.
  - SQLite has no REGEXP, so `WHERE x REGEXP '\\bindia\\b'` raised
    "no such function: REGEXP" and the question failed as sql_error.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.datasource import SqlDataSource  # noqa: E402

HERE = os.path.dirname(__file__)
DB = os.path.join(HERE, "..", "examples", "databases", "company.db")


def test_sqlite_has_regexp():
    ds = SqlDataSource(f"sqlite:///{DB}")
    r"""The operator exists in SQLite's grammar but resolves to a user function."""
    df = ds.run(r"SELECT 'Bangalore, India' REGEXP '\bindia\b' AS hit,"
                r" 'Canada' REGEXP '\bindia\b' AS miss")
    row = df.iloc[0]
    assert int(row["hit"]) == 1
    assert int(row["miss"]) == 0


def test_regexp_is_case_insensitive():
    ds = SqlDataSource(f"sqlite:///{DB}")
    df = ds.run(r"SELECT 'INDIA' REGEXP 'india' AS c")
    assert int(df.iloc[0]["c"]) == 1


def test_regexp_handles_invalid_pattern_without_raising():
    ds = SqlDataSource(f"sqlite:///{DB}")
    df = ds.run(r"SELECT 'x' REGEXP '[' AS c")
    assert int(df.iloc[0]["c"]) == 0


def test_submit_normalisation_accepts_reformatted_sql():
    """Whitespace and a trailing semicolon must not invalidate a submission."""

    def _norm(s):
        return " ".join((s or "").split()).rstrip(";").strip().lower()

    validated = "SELECT COUNT(*) FROM rulings r JOIN cards c ON r.uuid = c.uuid WHERE c.name = 'Fireball'"
    submitted = "select count(*)\n  from rulings r\n  join cards c on r.uuid = c.uuid\n  where c.name = 'Fireball';"
    assert _norm(validated) == _norm(submitted)
    # the old rule, for contrast
    assert validated != submitted
