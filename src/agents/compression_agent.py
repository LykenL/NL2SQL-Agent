"""Distil a finished conversation into one long-term note.

The previous version could not run at all: it required GEMINI_API_KEY, then called
model "gemma4:31b-cloud" through a client now pointed at DeepSeek, and slept 15 seconds
to "respect rate limits". Nothing ever called it either, so the vector index stayed
empty no matter how much was discussed.

This version uses whatever model the agent itself uses, does not sleep, keeps no
module-level credentials, and is safe to call repeatedly: the caller records how many
messages have been distilled, so unchanged history is never re-summarised.
"""

from __future__ import annotations

import re

from src.core.memory_manager import SQLiteMemory, VectorMemory

_PROMPT = """You are distilling a finished analytics conversation into a note that a
future session can rely on. Write it as a short brief, not a transcript.

Keep, when present:
- the business definitions that were settled (for example "revenue means invoice
  totals, not line items")
- which tables or columns turned out to be the right ones, and which were misleading
- the final answer, with its number and the shape of the result
- corrections the user made to your interpretation

Drop greetings, restatements of the question, and anything about tool usage.
Write at most 120 words of plain prose. No headings, no bullet symbols, no code.
If the conversation contains no lasting insight, reply exactly: NOTHING_TO_KEEP

Conversation:
{transcript}
"""


def _join_turns(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "User" if m.get("role") == "user" else "Assistant"
        text = re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
        if text:
            lines.append(f"{role}: {text[:1200]}")
    return "\n".join(lines)


def compress_session_memory(
    session_id: str,
    *,
    data_source: str | None = None,
    min_new_messages: int = 4,
    memory: "SQLiteMemory | None" = None,
    vector: "VectorMemory | None" = None,
    client=None,
    model: str | None = None,
) -> dict:
    """Summarise the part of a session that has not been summarised yet.

    Returns {"status": "stored" | "skipped" | "empty" | "error", ...}. Never raises:
    this runs after the user already has their answer, and losing a note must not
    become a visible failure.
    """
    try:
        memory = memory or SQLiteMemory()
        state = memory.compression_state(session_id)
        if state["messages"] < 2:
            return {"status": "empty", "reason": "session has no conversation yet"}
        new = state["messages"] - state["compressed_upto"]
        if new < min_new_messages:
            return {
                "status": "skipped",
                "reason": f"{new} new messages (min {min_new_messages})",
                "messages": state["messages"],
            }

        messages = memory.get_messages(session_id, limit=200)
        transcript = _join_turns(messages)
        if not transcript.strip():
            return {"status": "empty", "reason": "no text in transcript"}

        if client is None or model is None:
            from src.core import tracing

            client = client or tracing.openai_client()
            model = model or tracing.llm_model()

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _PROMPT.format(transcript=transcript)}],
            temperature=0.0,
        )
        note = (response.choices[0].message.content or "").strip()

        if not note or "NOTHING_TO_KEEP" in note:
            memory.mark_compressed(session_id, state["messages"])
            return {"status": "empty", "reason": "nothing worth keeping"}

        vector = vector or VectorMemory()
        # One note per session, keyed by session id: re-compressing overwrites that
        # note instead of appending a near-duplicate every few turns.
        vector.add_memory(
            note,
            metadata={
                "session_id": session_id,
                "data_source": data_source,
                "kind": "session_note",
            },
            doc_id=f"session:{session_id}",
        )
        memory.mark_compressed(session_id, state["messages"])
        return {"status": "stored", "note": note, "messages": state["messages"]}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}


def compress_in_background(session_id: str, data_source: str | None = None, **kwargs) -> None:
    """Fire-and-forget wrapper: the chat response must not wait for a summary."""
    import threading

    def _run():
        compress_session_memory(session_id, data_source=data_source, **kwargs)

    threading.Thread(target=_run, name=f"compress-{session_id}", daemon=True).start()
