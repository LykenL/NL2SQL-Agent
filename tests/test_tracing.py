"""Tracing must be optional and must shape payloads sensibly.

The app has to run with no Langfuse credentials (CI, pytest, local dev), so most
of what matters here is the zero-config path plus the payload helpers.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core import agent_loop, tracing  # noqa: E402


def test_disabled_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing.enabled() is False
    assert tracing.client() is None


def test_enabled_with_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.delenv("LANGFUSE_TRACING", raising=False)
    assert tracing.enabled() is True


def test_both_keys_required(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing.enabled() is False


def test_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_TRACING", "off")
    assert tracing.enabled() is False


def test_null_span_is_usable(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with tracing.span("noop", as_type="tool") as s:
        s.update(input={"a": 1}, output="x")
    with tracing.turn(session_id="s1") as t:
        t.update(input={"question": "q"})
    tracing.flush()  # must not raise


def test_openai_client_falls_back_to_plain_sdk(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = tracing.openai_client()
    assert type(client).__module__.startswith("openai")


def test_client_requires_a_key(monkeypatch):
    """A missing key must fail loudly at the first call, not midway through a run."""
    for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    try:
        tracing.openai_client()
    except RuntimeError as exc:
        assert "LLM_API_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError without a key")


def test_model_and_endpoint_defaults_are_deepseek(monkeypatch):
    for name in ("AGENT_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert tracing.llm_model() == "deepseek-chat"
    assert tracing.llm_base_url() == "https://api.deepseek.com/v1"


def test_model_name_is_overridable(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "deepseek-reasoner")
    assert tracing.llm_model() == "deepseek-reasoner"


def test_system_prompt_is_replaced_in_traced_messages():
    msgs = [
        {"role": "system", "content": "x" * 5000},
        {"role": "user", "content": "how many rows"},
        {"role": "assistant", "content": "hi"},
    ]
    masked = agent_loop._mask_messages(msgs)
    assert masked[0]["content"] == agent_loop._SYSTEM_STUB
    # Only the system message is stubbed; the conversation is preserved verbatim.
    assert masked[1:] == msgs[1:]


def test_tool_inputs_are_clipped():
    code = agent_loop._tool_input("execute_python_code", {"code_string": "x" * 9000})
    assert len(code["code"]) == 4000
    sql = agent_loop._tool_input("run_query", {"sql": "select " + "y" * 5000})
    assert len(sql["sql"]) == 2000
    assert agent_loop._tool_input("describe_table", {"table": "mart_x"}) == {"table": "mart_x"}


def test_tool_output_reports_truncation():
    out = agent_loop._tool_output("z" * 5000)
    assert out["chars"] == 5000
    assert len(out["output"]) == 2000
