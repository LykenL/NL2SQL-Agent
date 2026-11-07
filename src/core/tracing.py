"""
Langfuse tracing — one place, zero configuration.

Design rules (see ~/.hermes/skills/devops/langfuse/references/instrumentation.md):

1. Tracing is optional. Without LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY every
   helper here is a no-op, so CI, pytest and local dev need no cloud account.
2. One trace = one chat turn. The root span is opened in /api/chat; everything the
   agent does nests underneath it.
3. Import Langfuse only in this module. Import it AFTER load_dotenv() (the openai
   wrapper reads the keys at import time), and import it BEFORE the OpenAI client
   is constructed, otherwise the model call cannot be patched into a generation.

The openai wrapper lives in `openai_client()`: call sites use

    from src.core.tracing import openai_client
    client = openai_client()

and get a Langfuse-traced client, or a plain one when keys are absent.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator, Optional

_TRUTHY = {"1", "true", "yes", "on"}


def host() -> str:
    """The Langfuse server URL.

    Both names are accepted because projects created in the US region hand out
    LANGFUSE_BASE_URL, and the SDK itself reads that name when it initialises.
    """
    return (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")
            or "https://cloud.langfuse.com")


def enabled() -> bool:
    """Tracing is on only when both keys are present."""
    if os.getenv("LANGFUSE_TRACING", "").lower() in {"0", "false", "off", "no"}:
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))


def client() -> Optional[Any]:
    """The Langfuse client, or None. Never raises: a broken tracer must not
    take down a request."""
    if not enabled():
        return None
    try:
        from langfuse import get_client

        # Pin the server explicitly: the SDK prefers LANGFUSE_BASE_URL over
        # LANGFUSE_HOST, and a stale one in .env would silently win.
        return get_client(public_key=os.getenv("LANGFUSE_PUBLIC_KEY"))
    except Exception as exc:  # pragma: no cover - only with a broken install
        print(f"[tracing] disabled: {type(exc).__name__}: {exc}")
        return None


def openai_client() -> Any:
    """An OpenAI-compatible client that reports to Langfuse as a generation.

    Uses langfuse's drop-in OpenAI wrapper when tracing is configured. It is a
    subclass of openai.OpenAI, so every caller that already treats the result as
    a plain client keeps working.
    """
    kwargs = {
        "api_key": os.getenv("OLLAMA_API_KEY", "ollama"),
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "default_headers": {"ngrok-skip-browser-warning": "true"},
    }
    if enabled():
        try:
            from langfuse.openai import OpenAI as TracedOpenAI

            return TracedOpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover
            print(f"[tracing] openai wrapper unavailable, using plain client: {exc}")
    from openai import OpenAI

    return OpenAI(**kwargs)


@contextlib.contextmanager
def current_context(observation: Any) -> Any:
    """The OTel context an observation lives in, so children take the same trace.

    Threadpool workers lose the ambient context (see the note in agent_loop);
    passing an explicit TraceContext keeps root, generations and tools in one
    trace regardless of which thread built them.
    """
    trace_id = getattr(observation, "trace_id", None)
    otel_span = getattr(observation, "_otel_span", None)
    span_ctx = otel_span.get_span_context() if otel_span is not None else None
    if not trace_id or span_ctx is None:
        return _NullContext()
    return _Context(trace_id, format(span_ctx.span_id, "016x"))


class _NullContext:
    def to_trace_context(self) -> None:
        return None


class _Context:
    def __init__(self, trace_id: str, parent_id: str) -> None:
        self.trace_id = trace_id
        self.parent_span_id = parent_id

    def to_trace_context(self) -> Any:
        from langfuse.types import TraceContext

        return TraceContext(trace_id=self.trace_id, parent_span_id=self.parent_span_id)


@contextlib.contextmanager
def otel_context(observation: Any) -> Any:
    """A TraceContext pinned to this observation, usable from any thread.

    The langfuse SDK resolves the parent from the ambient OTel context. A sync
    endpoint runs in a threadpool worker, and observations created on a later
    task there resolve to no parent — Langfuse then opens a SECOND trace and the
    turn is split in two (root alone, children in another trace). Passing an
    explicit trace_context at creation time removes that dependence.
    """
    trace_id = getattr(observation, "trace_id", None)
    otel_span = getattr(observation, "_otel_span", None)
    if not trace_id or otel_span is None:
        return _NullContext()
    span_ctx = otel_span.get_span_context()
    return _Context(trace_id, format(span_ctx.span_id, "016x"))


@contextlib.contextmanager
def span(name: str, as_type: str = "span", *, context: Any = None,
         **kwargs: Any) -> Iterator[Any]:
    """A child observation, or a do-nothing placeholder when tracing is off.

    Any observation created inside the block nests under it automatically.
    """
    lf = client()
    if lf is None:
        yield _NullSpan()
        return
    if context is not None and not isinstance(context, _NullContext):
        kwargs["trace_context"] = context.to_trace_context()
    with lf.start_as_current_observation(as_type=as_type, name=name, **kwargs) as obs:
        yield obs


@contextlib.contextmanager
def turn(session_id: Optional[str] = None, user_id: Optional[str] = None,
         tags: Optional[list[str]] = None) -> Iterator[Any]:
    """Root span for one chat turn. Remember to set input= (question only)."""
    lf = client()
    if lf is None:
        yield _NullSpan()
        return

    from langfuse import propagate_attributes

    # propagate_attributes applies session/user/tags to every observation emitted
    # inside the block. Setting them on the root span alone leaves generations and
    # tool calls — and the Sessions view — without them.
    with lf.start_as_current_observation(as_type="span", name="chat-turn") as root:
        with propagate_attributes(
            session_id=session_id,
            user_id=user_id,
            tags=tags,
            version=os.getenv("APP_VERSION") or None,
        ):
            yield root


def flush() -> None:
    """Ship queued observations. Short-lived processes must call this."""
    lf = client()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception as exc:  # pragma: no cover
        print(f"[tracing] flush failed: {exc}")


class _NullSpan:
    """Stand-in so call sites never branch on tracing being enabled."""

    def update(self, *args: Any, **kwargs: Any) -> "_NullSpan":
        return self

    def end(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False
