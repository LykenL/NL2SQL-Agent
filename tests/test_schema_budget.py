"""A big schema must reach the model whole, and a big catalogue must stay small.

The failure this prevents: `get_database_schema` returned more than 6000 characters, the
generic clipper kept the head and tail and dropped the middle, and the tables in the middle
stopped existing as far as the model was concerned — so it invented table names. A schema's
middle is not formatting, it is the list of what exists.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.agent_loop import (  # noqa: E402
    _MAX_SCHEMA_CONTENT,
    _MAX_TOOL_CONTENT,
    _clip_limit,
    _clip_tool_content,
    _trim_history,
)
from src.core.datasource import DataSource  # noqa: E402


class BigSchemaSource(DataSource):
    """20 tables with descriptions — the shape of a real customer database."""

    dialect = "bigquery"
    label = "proj.big_dataset"

    TABLES = 20
    COLUMNS = 12

    def schema_json(self):
        import json

        out = {}
        for t in range(self.TABLES):
            out[f"table_{t:02d}_with_a_longish_name"] = {
                "columns": [
                    {"name": f"column_{c:02d}", "type": "STRING"} for c in range(self.COLUMNS)
                ],
                "description": f"Table {t} of the warehouse, holding one row per entity.",
                "qualified_name": f"proj.big_dataset.table_{t:02d}_with_a_longish_name",
            }
        return json.dumps(out, indent=2)

    def run(self, sql, max_rows=1000):
        import pandas as pd

        return pd.DataFrame({"x": [1]})


def test_full_schema_is_not_clipped_at_the_tool_budget():
    src = BigSchemaSource()
    schema = src.schema_json()
    assert len(schema) > 10_000, "fixture must be big enough to trip the old limit"

    clipped = _clip_tool_content(schema, _clip_limit("get_database_schema"))
    assert clipped == schema, "a schema must arrive complete"
    assert "omitted" not in clipped
    # every table survives, including the ones in the middle
    for t in range(src.TABLES):
        assert f"table_{t:02d}_with_a_longish_name" in clipped


def test_catalogue_stays_small_for_the_same_database():
    src = BigSchemaSource()
    index = src.table_index()
    assert len(index) < 4000, len(index)
    assert index.count("\n") == src.TABLES  # header + one line per table
    assert "20 tables" in index
    assert "column_00" not in index, "the catalogue must not carry columns"


def test_query_previews_keep_the_smaller_budget():
    text = "x" * 50_000
    assert _clip_limit("run_query") == _MAX_TOOL_CONTENT
    assert len(_clip_tool_content(text, _clip_limit("run_query"))) < _MAX_TOOL_CONTENT + 200


def test_trim_history_sacrifices_query_results_before_schema():
    schema = "S" * 30_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "name": "get_database_schema", "content": schema},
        {"role": "tool", "name": "run_query", "content": "Q" * 20_000},
        {"role": "tool", "name": "run_query", "content": "R" * 20_000},
        {"role": "tool", "name": "run_query", "content": "T" * 20_000},
        {"role": "tool", "name": "run_query", "content": "U" * 20_000},
    ]
    _trim_history(messages, budget=60_000)

    assert messages[1]["content"] == schema, "schema must survive the trim"
    assert any("earlier result elided" in m["content"] for m in messages[2:])


def test_tools_expose_list_tables_and_describe():
    from src.core.agent_tools import create_agent_tools

    _schema, tool_map = create_agent_tools(source=BigSchemaSource())
    assert "list_tables" in tool_map and "describe_table" in tool_map

    out = tool_map["list_tables"]()
    assert "20 tables" in out and "table_00_with_a_longish_name" in out

    one = tool_map["describe_table"]("table_07_with_a_longish_name")
    assert "column_00" in one and "table_00" not in one


def test_list_tables_is_exempt_from_the_small_budget_too():
    assert _clip_limit("list_tables") == _MAX_SCHEMA_CONTENT
    assert _clip_limit("describe_table") == _MAX_SCHEMA_CONTENT
