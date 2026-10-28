"""End-to-end enforcement test for the semantic layer (R1).

A scripted fake LLM first proposes the wrong revenue definition, then the right
one. The registry must reject the first before it reaches the sandbox, feed the
violation back to the model, and let the corrected script through.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.core.agent_loop as loop  # noqa: E402
from src.core.semantic_registry import Metric, SemanticRegistry  # noqa: E402

WRONG = "import pandas as pd\nprint(df['UnitPrice'].sum())"
RIGHT = 'print(df["Total"].sum())  # SUM(Invoice.Total)'


class _Mem:
    def __init__(self, *a, **k): pass
    def get_recent_context(self, *a, **k): return []
    def add_message(self, *a, **k): pass


class _Vec:
    def __init__(self, *a, **k): pass
    def search_memory(self, *a, **k): return []


class _Fn:
    def __init__(self, name, args): self.name, self.arguments = name, args


class _TC:
    def __init__(self, i, name, args): self.id, self.type, self.function = i, "function", _Fn(name, args)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.role, self.content, self.tool_calls = "assistant", content, tool_calls


class _Resp:
    def __init__(self, m): self.choices = [type("C", (), {"message": m})()]


class _ScriptedClient:
    """Emits the scripted turns, one per create() call."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls += 1
        turn = self.turns.pop(0) if self.turns else _Msg("done")
        return _Resp(turn)


def _tool_turn(code, call_id):
    return _Msg(None, [_TC(call_id, "execute_python_code", json.dumps({"code_string": code}))])


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    path = tmp_path / "reg.db"
    monkeypatch.setenv("SEMANTIC_REGISTRY_DB", str(path))
    monkeypatch.setenv("SEMANTIC_ENFORCE", "on")
    reg = SemanticRegistry(str(path))
    reg.upsert(Metric(
        id="revenue", display_name="营收", expr="SUM(Invoice.Total)",
        aliases=["营收", "revenue"], forbidden_columns=["UnitPrice", "Quantity"],
    ))
    reg.confirm("revenue", by="lyken", evidence_query_id="q_001")
    return reg


def _run(monkeypatch, turns):
    client = _ScriptedClient(turns)
    monkeypatch.setattr(loop, "SQLiteMemory", _Mem)
    monkeypatch.setattr(loop, "VectorMemory", _Vec)
    monkeypatch.setattr(loop, "OpenAI", lambda *a, **k: client)

    executed = []

    def tool(code_string="", **kw):
        executed.append(code_string)
        return "OUTPUT: 1.0"

    text, code, _, _ = loop.run_agent_loop(
        "今年的营收是多少", "test-session", [], {"execute_python_code": tool}
    )
    return text, code, executed, client.calls


def test_wrong_definition_is_blocked_then_corrected(registry, monkeypatch):
    text, code, executed, calls = _run(
        monkeypatch,
        [_tool_turn(WRONG, "c1"), _tool_turn(RIGHT, "c2"), _Msg("## 结论\n营收 $2,328.60。")],
    )

    # The wrong script never reached the sandbox; only the corrected one ran.
    assert executed == [RIGHT], executed
    assert code == RIGHT
    assert calls == 3


def test_violation_text_is_authoritative(registry, monkeypatch):
    # With only the wrong turn, the loop ends after the block — the reply must
    # still be prose-only and the sandbox untouched.
    text, code, executed, _ = _run(monkeypatch, [_tool_turn(WRONG, "c1"), _Msg("口径需要确认。")])
    assert executed == []
    assert code == WRONG  # captured for the Generated Script pane, not executed
    assert "```" not in text


def test_enforcement_can_be_disabled(registry, monkeypatch):
    monkeypatch.setenv("SEMANTIC_ENFORCE", "off")
    _, _, executed, _ = _run(monkeypatch, [_tool_turn(WRONG, "c1"), _Msg("done")])
    assert executed == [WRONG]
