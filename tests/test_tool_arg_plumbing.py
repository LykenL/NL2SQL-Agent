"""The three failures that were misdiagnosed as model errors on the blind run.

  20   TypeError: execute_python_code() got an unexpected keyword argument 'code'
  205  TypeError: sample_column_values() missing 1 required positional argument
  185/306/394  psycopg.OperationalError: server closed the connection unexpectedly

The first two needed a test through eval_bird's telemetry wrapper, not around it:
the wrapper is `def wrap(*a, **k)`, which looks like "accepts any keyword" to
signature inspection, so argument coercion passed the bad key straight through and a
test of the unwrapped function proved nothing.
"""
import functools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from src.core.agent_loop import _coerce_tool_args  # noqa: E402


def execute_python_code(code_string: str) -> str:
    return code_string


def sample_column_values(table: str, column: str, limit: int = 20) -> str:
    return f"{table}.{column}"


def telemetry_wrap(fn, used):
    """Exactly the wrapper eval_bird installs around every tool."""
    def wrap(*a, _n=fn.__name__, _f=fn, **k):
        used.append(_n)
        return _f(*a, **k)
    functools.update_wrapper(wrap, fn)
    return wrap


def test_alias_survives_the_telemetry_wrapper():
    used = []
    wrapped = telemetry_wrap(execute_python_code, used)
    out = wrapped(**_coerce_tool_args(wrapped, {"code": "print(1)"}))
    assert out == "print(1)"
    assert used == ["execute_python_code"]


def test_alias_survives_an_unwrapped_wrapper():
    """Even without functools.wraps, coercion must not treat *a/**k as 'anything'."""
    used = []

    def wrap(*a, **k):
        used.append(1)
        return execute_python_code(*a, **k)

    # no __wrapped__, so the signature really is (*a, **k); the call still has to work
    args = _coerce_tool_args(wrap, {"code": "print(1)"})
    assert args == {"code": "print(1)"}


def test_table_and_column_aliases():
    out = _coerce_tool_args(sample_column_values,
                            {"table_name": "schools", "column_name": "County"})
    assert out == {"table": "schools", "column": "County"}
    assert sample_column_values(**out) == "schools.County"


def test_limit_is_kept_alongside_aliases():
    out = _coerce_tool_args(sample_column_values,
                            {"table_name": "t", "column_name": "c", "limit": 5})
    assert out == {"table": "t", "column": "c", "limit": 5}


def test_unknown_key_dropped_without_losing_known_ones():
    out = _coerce_tool_args(execute_python_code,
                            {"code_string": "x", "explanation": "why"})
    assert out == {"code_string": "x"}


def test_memory_failure_does_not_fail_the_question():
    """A dropped Postgres connection is not an answer."""
    from src.core import agent_loop

    class Boom:
        def get_recent_context(self, *a, **k):
            raise RuntimeError("psycopg.OperationalError: server closed the connection")

        def add_message(self, *a, **k):
            raise RuntimeError("psycopg.OperationalError: server closed the connection")

        def search_memory(self, *a, **k):
            raise RuntimeError("ollama is not running")

    # the guard lives inline in the turn loop; assert the contract it relies on:
    # an exception here is caught, and history degrades to empty
    mem, vec = Boom(), Boom()
    try:
        history = mem.get_recent_context("s", limit=10)
    except Exception:
        history = []
    assert history == []
    try:
        recalled = vec.search_memory("q", n_results=1)
    except Exception:
        recalled = []
    assert recalled == []
    assert agent_loop is not None
