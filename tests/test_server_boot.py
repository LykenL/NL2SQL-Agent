"""The app module must import.

Adding `@app.exception_handler` above `app = FastAPI(...)` broke the entire server with
NameError at import time, and nothing caught it: every test imports the pieces, none
imported the assembled app, and `python -m py_compile` only checks syntax. A production
deploy would have failed the same way.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_server_imports_and_registers_the_error_handler():
    from src.backend import server

    assert server.app is not None
    # the handler must actually be registered, not merely defined
    assert Exception in server.app.exception_handlers


def test_error_body_has_no_credentials():
    from src.backend.server import _scrub

    out = _scrub("could not connect to postgresql://alice:hunter2@db.example.com/x")
    assert "hunter2" not in out
    assert "alice" not in out

    out2 = _scrub("GET https://api.example.com?api_key=sk-abc123&x=1 failed")
    assert "sk-abc123" not in out2
