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
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from eval_bird import _isolated_worker, run_with_deadline  # noqa: E402

# A row that evaluate_one can score without touching a database or the model:
# a valid gold SQL and an identical prediction means "correct", so the happy path
# is exercised end to end in a child process.
_TRIVIAL = {
    "id": -1, "db": "student_club", "difficulty": "simple",
    "question": "How many rows are in the one-row table?", "evidence": "",
    "sql": "SELECT 1",
}


def test_isolated_worker_runs_a_real_question():
    status, value = run_with_deadline(dict(_TRIVIAL), 12.0, timeout=120)
    assert status == "ok", value
    assert isinstance(value, dict)


def test_hung_question_is_cut_off_and_reported():
    # a question whose work outlives its deadline must come back as "timeout",
    # not block until the work finishes
    start = time.time()
    status, value = run_with_deadline(dict(_TRIVIAL), 12.0, timeout=0.3)
    elapsed = time.time() - start
    assert status == "timeout"
    assert value is None
    assert elapsed < 30, f"deadline not enforced ({elapsed:.1f}s)"


def test_worker_failure_is_returned_not_raised():
    # a malformed row makes evaluate_one raise; that must come back as data
    status, value = run_with_deadline({"id": 1}, 12.0, timeout=120)
    assert status in {"ok", "error"}
    if status == "error":
        assert isinstance(value, str) and value
    assert _isolated_worker is not None
