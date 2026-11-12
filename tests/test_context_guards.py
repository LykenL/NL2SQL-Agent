"""Two failures that cost whole questions on the blind run, both engineering:

  259  execute_python_code() got an unexpected keyword argument 'code'
  404  request of 1.88M tokens against a 1,048,576 limit

Neither was a reasoning failure. One wrong argument key and an unbounded transcript
killed the questions outright.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.agent_loop import (  # noqa: E402
    _clip_tool_content,
    _coerce_tool_args,
    _trim_history,
    _MAX_TOOL_CONTENT,
)


def _execute_python_code(code_string: str) -> str:
    return f"ran {code_string!r}"


def _run_query(sql: str, max_rows: int = 1000) -> str:
    return "rows"


def test_alias_code_maps_to_code_string():
    out = _coerce_tool_args(_execute_python_code, {"code": "print(1)"})
    assert out == {"code_string": "print(1)"}


def test_correct_argument_passes_through():
    out = _coerce_tool_args(_execute_python_code, {"code_string": "print(1)"})
    assert out == {"code_string": "print(1)"}


def test_unknown_argument_is_dropped_not_fatal():
    out = _coerce_tool_args(_run_query, {"sql": "SELECT 1", "explanation": "why"})
    assert out == {"sql": "SELECT 1"}


def test_alias_query_maps_to_sql():
    out = _coerce_tool_args(_run_query, {"query": "SELECT 1"})
    assert out == {"sql": "SELECT 1"}


def test_kwargs_function_keeps_everything():
    def f(**kwargs):
        return kwargs

    assert _coerce_tool_args(f, {"anything": 1}) == {"anything": 1}


def test_long_tool_result_is_clipped_both_ends():
    text = "HEAD" + ("x" * 50_000) + "TAIL"
    out = _clip_tool_content(text)
    assert len(out) < _MAX_TOOL_CONTENT + 200
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "chars omitted" in out


def test_short_tool_result_is_untouched():
    assert _clip_tool_content("small") == "small"


def test_history_is_trimmed_when_over_budget_but_keeps_structure():
    messages = [{"role": "system", "content": "sys"}]
    for i in range(20):
        messages.append({"role": "assistant", "content": [{"tool_call": i}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 30_000})
    messages.append({"role": "user", "content": "the question"})

    _trim_history(messages, budget=100_000)

    total = sum(len(str(m.get("content") or "")) for m in messages)
    assert total < 400_000
    # assistant tool calls must survive: a tool message with no matching call is rejected
    assert sum(1 for m in messages if m["role"] == "assistant") == 20
    assert sum(1 for m in messages if m["role"] == "tool") == 20
    # newest results stay intact, the question is never dropped
    assert messages[-1]["content"] == "the question"
    elided = [m for m in messages if m["role"] == "tool" and "elided" in str(m["content"])]
    assert elided, "expected the oldest results to be elided"
    assert len(str(messages[-2]["content"])) > 20_000, "newest result must not be elided"


def test_history_under_budget_is_untouched():
    messages = [{"role": "system", "content": "sys"},
                {"role": "tool", "tool_call_id": "c0", "content": "small"}]
    before = [dict(m) for m in messages]
    _trim_history(messages, budget=100_000)
    assert messages == before
