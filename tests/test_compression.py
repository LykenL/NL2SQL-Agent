"""Compression: distil a session into one long-term note, exactly once per change.

The shipped version could never have produced a note: it bailed out unless
GEMINI_API_KEY was set, then asked a DeepSeek endpoint for model "gemma4:31b-cloud",
and slept 15 seconds first. No caller existed, so the index held zero vectors.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.compression_agent import compress_session_memory  # noqa: E402
from src.core.memory_manager import DatabaseMemory  # noqa: E402


class FakeVector:
    def __init__(self):
        self.store = {}

    def add_memory(self, logic_content, metadata=None, doc_id=None):
        self.store[doc_id] = {"text": logic_content, "metadata": metadata or {}}
        return doc_id


class FakeClient:
    """Minimal stand-in for the OpenAI client: records prompts, returns a fixed note."""

    def __init__(self, reply="Revenue means invoice totals; the answer was 47 schools."):
        self.reply = reply
        self.calls = []

    @property
    def chat(self):
        return types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        msg = types.SimpleNamespace(content=self.reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def _seed(mem, sid, turns=3, source="gold:demo"):
    mem.touch_session(sid, source)
    for i in range(turns):
        mem.add_message(sid, "user", f"question {i}")
        mem.add_message(sid, "assistant", f"answer {i}")


def test_stores_one_note_and_marks_progress(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    vec, client = FakeVector(), FakeClient()
    _seed(mem, "s1")

    out = compress_session_memory("s1", data_source="gold:demo", memory=mem, vector=vec,
                                  client=client, model="m")
    assert out["status"] == "stored"
    assert "session:s1" in vec.store
    assert vec.store["session:s1"]["metadata"]["data_source"] == "gold:demo"
    assert mem.compression_state("s1")["compressed_upto"] == 6
    assert len(client.calls) == 1


def test_does_not_rerun_on_unchanged_history(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    vec, client = FakeVector(), FakeClient()
    _seed(mem, "s2")

    compress_session_memory("s2", memory=mem, vector=vec, client=client, model="m")
    again = compress_session_memory("s2", memory=mem, vector=vec, client=client, model="m")
    assert again["status"] == "skipped"
    assert len(client.calls) == 1, "unchanged history must not be summarised twice"


def test_new_turns_trigger_a_second_compression(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    vec, client = FakeVector(), FakeClient()
    _seed(mem, "s3")
    compress_session_memory("s3", memory=mem, vector=vec, client=client, model="m")

    # one further exchange is 2 messages, below the 4-message threshold
    for q in ("one more question", "and another"):
        mem.add_message("s3", "user", q)
        mem.add_message("s3", "assistant", "answer")
    out = compress_session_memory("s3", memory=mem, vector=vec, client=client, model="m")
    assert out["status"] == "stored"
    assert len(client.calls) == 2
    # same doc id: the note is replaced, not duplicated
    assert list(vec.store) == ["session:s3"]


def test_nothing_to_keep_marks_progress_without_storing(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    vec = FakeVector()
    client = FakeClient(reply="NOTHING_TO_KEEP")
    _seed(mem, "s4")

    out = compress_session_memory("s4", memory=mem, vector=vec, client=client, model="m")
    assert out["status"] == "empty"
    assert vec.store == {}
    assert mem.compression_state("s4")["compressed_upto"] == 6


def test_empty_session_is_not_sent_to_the_model(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    client = FakeClient()
    out = compress_session_memory("nope", memory=mem, vector=FakeVector(), client=client, model="m")
    assert out["status"] == "empty"
    assert client.calls == []


def test_model_failure_is_returned_not_raised(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    _seed(mem, "s5")

    class Boom:
        @property
        def chat(self):
            def _raise(**kwargs):
                raise RuntimeError("upstream 500")
            return types.SimpleNamespace(completions=types.SimpleNamespace(create=_raise))

    out = compress_session_memory("s5", memory=mem, vector=FakeVector(), client=Boom(), model="m")
    assert out["status"] == "error"
    assert "upstream 500" in out["error"]
    # progress untouched, so a later turn retries rather than silently losing the note
    assert mem.compression_state("s5")["compressed_upto"] == 0


def test_prompt_includes_the_conversation(tmp_path):
    mem = DatabaseMemory(f"sqlite:///{tmp_path}/c.db")
    client = FakeClient()
    _seed(mem, "s6")
    compress_session_memory("s6", memory=mem, vector=FakeVector(), client=client, model="m")
    prompt = client.calls[0]["messages"][0]["content"]
    assert "question 0" in prompt and "answer 0" in prompt
    assert client.calls[0]["model"] == "m"
