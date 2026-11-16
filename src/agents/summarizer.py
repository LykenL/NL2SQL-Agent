"""Rolling summary for long conversations.

Goal: a 40-turn conversation must not lose its first 34 turns just because only the last
few messages fit in the prompt.

What this does *not* do: delete anything. The full transcript stays in `history`, so any
turn can still be read back or audited; the summary only decides what is replayed into the
model's context. The brief is merged with the previous brief, so it stays bounded (a few
hundred words) no matter how long the conversation gets.

No embeddings and no vector store: DeepSeek exposes no embeddings endpoint, so the brief is
plain text on the session row.
"""

from __future__ import annotations

import re

from src.core.memory_manager import SQLiteMemory

_PROMPT = """You maintain a running brief of an analytics conversation. You are given the
previous brief (possibly empty) and the newest turns since that brief was written.

Produce the UPDATED brief. Merge, do not append: keep it under 250 words, in plain prose.

Always preserve, when they appeared:
- metric definitions and business rules that were settled ("revenue means invoice totals")
- which tables or columns were the right ones, and which turned out misleading
- concrete numbers that were answered, with what they measure
- corrections the user made to your interpretation

Drop greetings, restatements of the question, tool mechanics, and anything transient.
If the new turns add nothing durable, return the previous brief unchanged.

Previous brief:
{previous}

New turns:
{transcript}
"""


def _join_turns(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "User" if m.get("role") == "user" else "Assistant"
        text = re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
        if text:
            lines.append(f"{role}: {text[:1500]}")
    return "\n".join(lines)


def summarize_session(
    session_id: str,
    *,
    min_new_messages: int = 8,
    recent_keep: int = 6,
    memory: SQLiteMemory | None = None,
    client=None,
    model: str | None = None,
) -> dict:
    """Fold the un-summarised turns into the session brief.

    Returns {"status": "stored" | "skipped" | "empty" | "error", ...} and never raises:
    this runs after the user already has their answer.
    """
    try:
        memory = memory or SQLiteMemory()
        state = memory.compression_state(session_id)
        if state["messages"] < 2:
            return {"status": "empty", "reason": "session has no conversation yet"}

        upto = state["compressed_upto"]
        pending = state["messages"] - upto
        # Keep the newest turns raw: they are what follow-up questions refer to.
        if pending - recent_keep < min_new_messages:
            return {
                "status": "skipped",
                "reason": f"{pending} un-summarised messages (need {min_new_messages + recent_keep})",
                "messages": state["messages"],
            }

        all_messages = memory.get_messages(session_id, limit=2000)
        # Compress everything except the newest `recent_keep` messages.
        boundary = max(0, len(all_messages) - recent_keep)
        chunk = all_messages[upto:boundary]
        if not chunk:
            return {"status": "skipped", "reason": "nothing new to fold in"}

        transcript = _join_turns(chunk)
        previous = memory.get_summary(session_id)

        if client is None or model is None:
            from src.core import tracing

            client = client or tracing.openai_client()
            model = model or tracing.llm_model()

        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(previous=previous or "(none)", transcript=transcript),
            }],
            temperature=0.0,
        )
        summary = (response.choices[0].message.content or "").strip()

        if not summary:
            return {"status": "empty", "reason": "model returned no summary"}

        memory.set_summary(session_id, summary, boundary)
        return {
            "status": "stored",
            "summary": summary,
            "folded": len(chunk),
            "compressed_upto": boundary,
            "total_messages": len(all_messages),
        }
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}


def summarize_in_background(session_id: str, **kwargs) -> None:
    """Fire-and-forget: the answer has already been returned."""
    import threading

    def _run():
        summarize_session(session_id, **kwargs)

    threading.Thread(target=_run, name=f"summary-{session_id}", daemon=True).start()
