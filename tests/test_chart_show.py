"""A chart must survive a stray .show(), including where IPython is absent.

Production broke with ModuleNotFoundError: No module named 'IPython' at
`chart.show()` — the renderer reads the `fig` / `chart` variable out of the namespace
and serialises it itself, so .show() was pure liability.

The local machine has IPython installed, so a test that merely calls .show() would pass
without the fix. These tests block the IPython import to reproduce the runtime image.
"""
import builtins
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.executor import safe_execute  # noqa: E402


@pytest.fixture
def no_ipython(monkeypatch):
    """Make `import IPython` fail, the way the deployed image does."""
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "IPython" or name.startswith("IPython."):
            raise ModuleNotFoundError("No module named 'IPython'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.delitem(sys.modules, "IPython", raising=False)


def test_plotly_show_is_a_noop_without_ipython(no_ipython):
    code = """
import plotly.graph_objects as go
fig = go.Figure(data=[go.Bar(x=['a','b'], y=[1,2])])
fig.show()
print('after show')
"""
    out = safe_execute(code, {"__name__": "__main__"})
    assert "after show" in out, out
    assert "ModuleNotFoundError" not in out, out


def test_altair_show_is_a_noop_without_ipython(no_ipython):
    code = """
import altair as alt
import pandas as pd
chart = alt.Chart(pd.DataFrame({'a':[1,2],'b':[3,4]})).mark_bar().encode(x='a', y='b')
chart.show()
print('chart assigned')
"""
    out = safe_execute(code, {"__name__": "__main__"})
    assert "chart assigned" in out, out
    assert "ModuleNotFoundError" not in out, out


def test_figure_survives_in_the_namespace_for_the_renderer(no_ipython):
    code = """
import plotly.graph_objects as go
fig = go.Figure(data=[go.Bar(x=['a'], y=[1])])
fig.show()
"""
    ns = {"__name__": "__main__"}
    safe_execute(code, ns)
    # the server captures this object and renders it; show() must not consume it
    assert "fig" in ns and hasattr(ns["fig"], "to_html")
    assert "plotly-graph-div" in ns["fig"].to_html(include_plotlyjs=False)
