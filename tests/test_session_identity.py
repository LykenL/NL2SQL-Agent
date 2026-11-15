"""Conversations must be findable under the data source the browser sends.

They were not: the chat endpoint stamped each row with `data_source.label`
("woven-surface-...-gold"), while the sidebar filtered by the spec the browser sends
("marts:woven-surface-...-gold"). The two never matched, so a real conversation with
real messages existed in the database and the list stayed empty — which reads as
"the session list is fake".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.datasource import BigQueryDataSource, SqlDataSource  # noqa: E402
from src.core.mart_source import MartSource  # noqa: E402
from src.core.memory_manager import DatabaseMemory  # noqa: E402

SPEC = "marts:woven-surface-511317-g0.data_copilot_dev_gold"


def test_spec_is_the_identity_not_the_label():
    src = MartSource(project="woven-surface-511317-g0", dataset="data_copilot_dev_gold")
    assert src.id == SPEC
    assert src.label == "woven-surface-511317-g0.data_copilot_dev_gold"

    bq = BigQueryDataSource(project="p", dataset="d")
    assert bq.id == "bigquery:p.d"

    sql = SqlDataSource("sqlite:///examples/databases/company.db")
    assert sql.id == "sqlite:///examples/databases/company.db"


def test_row_written_with_the_spec_is_listed_by_the_spec(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    mem.touch_session("conv1", SPEC)
    mem.add_message("conv1", "user", "how many schools?")

    listed = mem.get_all_sessions(data_source=SPEC)
    assert [s["id"] for s in listed] == ["conv1"]
    assert listed[0]["messages"] == 1


def test_legacy_rows_without_the_prefix_are_still_listed(tmp_path):
    """Rows written before the fix hold "proj.dataset"; the browser sends "marts:proj.dataset"."""
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    mem.touch_session("old", "woven-surface-511317-g0.data_copilot_dev_gold")

    listed = mem.get_all_sessions(data_source=SPEC)
    assert [s["id"] for s in listed] == ["old"]


def test_other_sources_are_not_listed(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    mem.touch_session("mine", SPEC)
    mem.touch_session("theirs", "marts:other-project.other_dataset")

    assert [s["id"] for s in mem.get_all_sessions(data_source=SPEC)] == ["mine"]
    assert len(mem.get_all_sessions()) == 2
