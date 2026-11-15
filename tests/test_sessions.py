"""Session listing, transcripts and deletion — the backend the sidebar needs.

Before this, GET /api/sessions returned {id, title} only: no timestamps, no message
count, no data source, and title was always the literal "Generating Title..." because
nothing ever called set_session_title. The UI could not render a usable list, load a
past conversation, or delete one.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.memory_manager import DatabaseMemory  # noqa: E402


def _mem(tmp_path):
    return DatabaseMemory(f"sqlite:///{tmp_path}/sessions_test.db")


def test_new_session_is_listed_with_derived_title(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("abc123", "gold:demo")
    m.add_message("abc123", "user", "How many schools are in Alameda County?")
    m.add_message("abc123", "assistant", "There are 47.")

    sessions = m.get_all_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == "abc123"
    assert s["title"] == "How many schools are in Alameda County?"
    assert s["messages"] == 2
    assert s["data_source"] == "gold:demo"
    assert s["created_at"] and s["last_at"]


def test_title_falls_back_to_first_question_and_is_truncated(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("s2")
    m.add_message("s2", "user", "x" * 200)
    title = m.get_all_sessions()[0]["title"]
    assert len(title) <= 61
    assert title.endswith("…")


def test_sessions_are_newest_first(tmp_path):
    m = _mem(tmp_path)
    for sid in ("a", "b", "c"):
        m.touch_session(sid)
        m.add_message(sid, "user", f"question {sid}")
    order = [s["id"] for s in m.get_all_sessions()]
    assert order[0] == "c" and order[-1] == "a"


def test_filter_by_data_source(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("g1", "gold:demo")
    m.touch_session("b1", "bigquery:project.dataset")
    only_gold = m.get_all_sessions(data_source="gold:demo")
    assert [s["id"] for s in only_gold] == ["g1"]
    assert len(m.get_all_sessions()) == 2


def test_transcript_round_trip(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("t1")
    m.add_message("t1", "user", "first")
    m.add_message("t1", "assistant", "second")
    msgs = m.get_messages("t1")
    assert [x["role"] for x in msgs] == ["user", "assistant"]
    assert [x["content"] for x in msgs] == ["first", "second"]
    assert all(x["timestamp"] for x in msgs)


def test_delete_removes_session_and_history(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("d1")
    m.add_message("d1", "user", "hello")
    m.delete_session("d1")
    assert m.get_all_sessions() == []
    assert m.get_messages("d1") == []


def test_touch_is_idempotent_and_keeps_first_data_source(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("i1", "gold:demo")
    m.touch_session("i1", "another:source")
    sessions = m.get_all_sessions()
    assert len(sessions) == 1
    # first source wins: a conversation does not migrate because the user switched DBs
    assert sessions[0]["data_source"] == "gold:demo"


def test_session_with_no_messages_still_listed(tmp_path):
    m = _mem(tmp_path)
    m.touch_session("empty", "gold:demo")
    sessions = m.get_all_sessions()
    assert len(sessions) == 1
    assert sessions[0]["messages"] == 0
    assert sessions[0]["title"] == "New conversation"


def test_migration_adds_data_source_to_an_old_table(tmp_path):
    """The deployed Postgres already has `sessions` without data_source."""
    import sqlite3

    path = f"{tmp_path}/old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, title TEXT, created_at TEXT)")
    con.execute("INSERT INTO sessions VALUES ('old1','legacy',NULL)")
    con.commit()
    con.close()

    m = DatabaseMemory(f"sqlite:///{path}")
    m.touch_session("old1", "gold:demo")
    sessions = m.get_all_sessions()
    assert sessions[0]["data_source"] == "gold:demo"
    assert sessions[0]["title"] == "legacy"
