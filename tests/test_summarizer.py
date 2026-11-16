"""Long conversations keep their beginning.

Only the last ten turns used to be replayed, so a 40-turn conversation silently lost its
first 30 — the definition agreed at the start, the number already computed, the column that
turned out wrong.

Now older turns are folded into a rolling brief while the newest turns stay raw, and the
full transcript is still stored: the brief decides what is replayed, never what is kept.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.summarizer import summarize_session  # noqa: E402
from src.core.memory_manager import DatabaseMemory  # noqa: E402


class FakeClient:
    def __init__(self, replies=None):
        self.replies = list(replies or ["BRIEF"])
        self.calls = []

    @property
    def chat(self):
        return types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
        )


def _seed(mem, sid, exchanges=10):
    mem.touch_session(sid, "gold:demo")
    for i in range(exchanges):
        mem.add_message(sid, "user", f"question {i}")
        mem.add_message(sid, "assistant", f"answer {i}")


def test_brief_covers_older_turns_and_keeps_newest_raw(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s1", exchanges=10)  # 20 messages

    out = summarize_session("s1", memory=mem, client=FakeClient(), model="m")
    assert out["status"] == "stored"
    assert out["folded"] == 14          # 20 - 6 newest kept raw
    assert out["compressed_upto"] == 14

    ctx = mem.context_for_prompt("s1", recent=6)
    assert ctx["summary"] == "BRIEF"
    assert len(ctx["messages"]) == 6
    assert ctx["messages"][-1]["content"] == "answer 9"


def test_transcript_is_never_deleted(tmp_path):
    """Plan A: the brief changes what is replayed, not what is stored."""
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s2", exchanges=10)
    summarize_session("s2", memory=mem, client=FakeClient(), model="m")

    assert len(mem.get_messages("s2")) == 20
    assert mem.get_messages("s2")[0]["content"] == "question 0"


def test_below_threshold_is_skipped_without_a_model_call(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s3", exchanges=2)  # 4 messages
    client = FakeClient()
    out = summarize_session("s3", memory=mem, client=client, model="m")
    assert out["status"] == "skipped"
    assert client.calls == []


def test_second_pass_merges_with_previous_brief(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s4", exchanges=10)
    client = FakeClient(["FIRST", "SECOND"])
    summarize_session("s4", memory=mem, client=client, model="m")

    for i in range(10, 16):
        mem.add_message("s4", "user", f"question {i}")
        mem.add_message("s4", "assistant", f"answer {i}")

    out = summarize_session("s4", memory=mem, client=client, model="m")
    assert out["status"] == "stored"
    assert mem.get_summary("s4") == "SECOND"
    # the previous brief was handed to the model, so the roll stays cumulative
    second_prompt = client.calls[1]["messages"][0]["content"]
    assert "FIRST" in second_prompt
    assert "question 10" in second_prompt
    # and it did not re-send turns already folded in
    assert "question 0" not in second_prompt


def test_unchanged_history_does_not_reprompt(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s5", exchanges=10)
    client = FakeClient()
    summarize_session("s5", memory=mem, client=client, model="m")
    again = summarize_session("s5", memory=mem, client=client, model="m")
    assert again["status"] == "skipped"
    assert len(client.calls) == 1


def test_model_failure_leaves_progress_untouched(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s6", exchanges=10)

    class Boom:
        @property
        def chat(self):
            def _raise(**kwargs):
                raise RuntimeError("upstream 500")
            return types.SimpleNamespace(completions=types.SimpleNamespace(create=_raise))

    out = summarize_session("s6", memory=mem, client=Boom(), model="m")
    assert out["status"] == "error"
    assert mem.compression_state("s6")["compressed_upto"] == 0
    assert mem.get_summary("s6") == ""


def test_empty_session_is_not_sent_to_the_model(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    client = FakeClient()
    out = summarize_session("nope", memory=mem, client=client, model="m")
    assert out["status"] == "empty"
    assert client.calls == []


def test_prompt_asks_to_merge_not_append(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/s.db")
    _seed(mem, "s7", exchanges=10)
    client = FakeClient()
    summarize_session("s7", memory=mem, client=client, model="m")
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Merge, do not append" in prompt
    assert "question 0" in prompt   # the oldest turns are what get folded


def test_migration_adds_summary_to_an_old_table(tmp_path):
    """The deployed table predates this column."""
    import sqlite3

    path = f"{tmp_path}/old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, title TEXT, created_at TEXT)")
    con.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, session_id TEXT, timestamp TEXT, role TEXT, content TEXT)")
    con.execute("INSERT INTO sessions VALUES ('old1','legacy',NULL)")
    for i in range(20):
        con.execute("INSERT INTO history (session_id,timestamp,role,content) VALUES ('old1','t','user',?)", (f"q{i}",))
    con.commit()
    con.close()

    mem = DatabaseMemory(f"sqlite:///{path}")
    out = summarize_session("old1", memory=mem, client=FakeClient(), model="m")
    assert out["status"] == "stored"
    assert mem.get_summary("old1") == "BRIEF"
