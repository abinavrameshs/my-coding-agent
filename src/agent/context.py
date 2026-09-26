"""Context compaction — summarises conversation history when it grows too large.

Fires automatically when prompt_tokens exceeds 80% of cfg.context_limit.
Can also be triggered manually via /compact.

Strategy: summarise all turns except the last two, replace them with a
single summary block so the model retains full context of the current task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from agent.config.config import Config

_SUMMARY_SYSTEM = """\
You are a conversation summariser. Given the history of a coding session between
a user and an AI agent, write a concise summary that preserves everything the
agent needs to continue working effectively.

Include:
- The user's original goal(s) and any sub-goals discovered along the way
- Decisions made and the reasoning behind them
- Files created, modified, or deleted (with their paths)
- Commands run and their notable outputs
- Any preferences or constraints the user stated
- Current state of in-progress work and what remains

Be factual and specific. Use bullet points. Omit pleasantries and filler.
"""

_KEEP_LAST_TURNS = 2  # number of full user/assistant turn pairs to keep verbatim


def _system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m["role"] == "system"]


def _non_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if m["role"] != "system"]


def _transcript_text(messages: list[dict[str, Any]]) -> str:
    """Convert messages to a readable transcript for summarisation."""
    lines: list[str] = []
    for m in messages:
        role = m["role"].upper()
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        if content and isinstance(content, str):
            lines.append(f"{role}: {content[:1000]}")
        # Tool calls / results are included as brief notes
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                lines.append(f"  [tool call: {fn.get('name', '?')}]")
        if m["role"] == "tool":
            lines.append(f"  [tool result: {str(content)[:200]}]")
    return "\n\n".join(lines)


async def compact_messages(
    messages: list[dict[str, Any]],
    cfg: "Config",
    client: "AsyncOpenAI",
) -> tuple[list[dict[str, Any]], int, int]:
    """Summarise *messages* in place and return (new_messages, msgs_before, msgs_after).

    Keeps the system prompt(s) and the last _KEEP_LAST_TURNS turn-pairs verbatim.
    Everything else is replaced with a summary block.

    Returns message counts (not token counts) — callers that want to show real
    token savings should track prompt_tokens from _last_usage themselves.
    """
    non_sys = _non_system(messages)
    sys_msgs = _system_messages(messages)

    # Identify "last N turns" — each turn = one user + one (or more) assistant messages
    turn_boundaries: list[int] = []  # indices of user messages in non_sys
    for i, m in enumerate(non_sys):
        if m["role"] == "user":
            turn_boundaries.append(i)

    msgs_before = len(non_sys)

    if len(turn_boundaries) <= _KEEP_LAST_TURNS:
        # Nothing worth compacting yet
        return messages, msgs_before, msgs_before

    # Split: to-summarise vs to-keep
    keep_from_idx = turn_boundaries[-_KEEP_LAST_TURNS]
    to_summarise = non_sys[:keep_from_idx]
    to_keep = non_sys[keep_from_idx:]

    transcript = _transcript_text(to_summarise)

    try:
        resp = await client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": f"Conversation to summarise:\n\n{transcript}"},
            ],
            temperature=0,
            max_tokens=1024,
        )
        summary = resp.choices[0].message.content or "(summary unavailable)"
    except Exception as e:
        summary = f"(compaction failed: {e})"

    summary_block = (
        f"## Conversation summary (compacted)\n\n{summary}\n\n"
        "_The above summarises the conversation so far. Continue from here._"
    )

    new_messages: list[dict[str, Any]] = [
        *sys_msgs,
        {"role": "user", "content": summary_block},
        {"role": "assistant", "content": "Understood. I have the full context from the summary and will continue."},
        *to_keep,
    ]

    # msgs_after counts non-system messages in the compacted history
    msgs_after = len(_non_system(new_messages))

    messages.clear()
    messages.extend(new_messages)
    return messages, msgs_before, msgs_after
