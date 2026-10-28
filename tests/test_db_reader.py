"""Regression test: schema extraction must work for SQLite.

`db_reader` used to pass `connect_timeout` to every engine; psycopg2 accepts it,
sqlite3 does not, so every SQLite database failed with
"'connect_timeout' is an invalid keyword argument for Connection()".
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.db_reader import extract_db_schema  # noqa: E402

CHINOOK = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "databases", "Chinook_Sqlite.sqlite")
)


def test_sqlite_schema_extraction():
    uri = f"sqlite:///{CHINOOK}"
    schema = extract_db_schema(uri, force_refresh=True)
    assert "Error extracting schema" not in schema, schema
    assert '"Invoice"' in schema
    assert '"InvoiceLine"' in schema
    # foreign keys come through, which is what the agent needs for JOINs
    assert "to_table" in schema


def test_unknown_path_returns_error_string_not_exception():
    schema = extract_db_schema("sqlite:////nonexistent/nope.db", force_refresh=True)
    assert "Error extracting schema" in schema
