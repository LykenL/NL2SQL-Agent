"""A hung question must not stall the run or lose the other results.

The blind run this protects against: 6 workers, one question stuck for 22 minutes,
every other result already computed and thrown away when the process was killed.

Two mechanisms are needed together:
  - ThreadPoolExecutor(threads) could not work: a thread that never returns cannot be
    killed, and ThreadPoolExecutor.shutdown(wait=True) — the `with` exit — waits for
    it again, which is precisely how the original run hung. A process can be stopped.
  - spawn, not fork: forking after the OpenAI/httpx clients have started threads
    segfaults the child on macOS (exit code -11, empty queue, no traceback).
  - the child target must be module level and take plain values, because spawn
    pickles it; a closure or lambda fails with "Can't get local object".

These tests inject their own worker. An earlier version drove the real evaluate_one,
which meant every `pytest` run made live model calls and wrote `bird_-1_*` sessions into
the production database.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from eval_bird import run_with_deadline  # noqa: E402


def _fast(row, sql_timeout):
    return {"id": row.get("id"), "ok": True}


def _hang(row, sql_timeout):
    time.sleep(60)
    return {"id": row.get("id"), "ok": True}


def _boom(row, sql_timeout):
    raise RuntimeError("worker exploded")


def test_fast_worker_returns_its_result():
    status, value = run_with_deadline({"id": 1}, 12.0, timeout=60, worker=_fast)
    assert status == "ok"
    assert value == {"id": 1, "ok": True}


def test_hung_worker_is_killed_at_the_deadline():
    start = time.time()
    status, value = run_with_deadline({"id": 2}, 12.0, timeout=2, worker=_hang)
    elapsed = time.time() - start
    assert status == "timeout"
    assert value is None
    assert elapsed < 30, f"deadline not enforced ({elapsed:.1f}s)"


def test_worker_crash_is_reported_not_raised():
    status, value = run_with_deadline({"id": 3}, 12.0, timeout=60, worker=_boom)
    assert status == "error"
    assert "worker exploded" in value


def test_child_accepts_an_injected_worker():
    """Guards the regression: the real worker calls the model and writes memory."""
    import inspect

    from eval_bird import _isolated_worker

    src = inspect.getsource(_isolated_worker)
    assert "worker or evaluate_one" in src
