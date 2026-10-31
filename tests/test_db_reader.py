"""Regression test: schema extraction must work for SQLite.

`db_reader` used to pass `connect_timeout` to every engine; psycopg2 accepts it,
sqlite3 does not, so every SQLite database failed with
"'connect_timeout' is an invalid keyword argument for Connection()".
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.db_reader import extract_db_schema  # noqa: E402


def _make_db(path) -> str:
    """A small SQLite file with a FK, built by the test (no repo fixture needed)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table artist (id integer primary key, name text not null);
        create table album (
            id integer primary key,
            title text not null,
            artist_id integer references artist(id)
        );
        insert into artist (name) values ('A'), ('B');
        insert into album (title, artist_id) values ('One', 1), ('Two', 2);
        """
    )
    conn.commit()
    conn.close()
    return f"sqlite:///{path}"


def test_sqlite_schema_extraction(tmp_path):
    uri = _make_db(tmp_path / "fixture.db")
    schema = extract_db_schema(uri, force_refresh=True)
    assert "Error extracting schema" not in schema, schema
    assert '"album"' in schema and '"artist"' in schema
    # foreign keys are what the agent needs to write JOINs
    assert "to_table" in schema and "artist" in schema


def test_unknown_path_returns_error_string_not_exception():
    schema = extract_db_schema("sqlite:////nonexistent/nope.db", force_refresh=True)
    assert "Error extracting schema" in schema
