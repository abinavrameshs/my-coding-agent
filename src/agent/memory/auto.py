"""Auto-memory extraction — pulls project facts from a conversation.

Uses the configured model (DeepSeek) to extract short factual notes that a
coding assistant should remember across sessions. Saves them as key-value pairs
to ~/.agent/memory/<project_hash>.json. On the next session the loader injects
them into the system prompt as "## Remembered context".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.config.config import Config

_SYSTEM_PROMPT = """\
You are a memory extraction assistant. Given a conversation between a user and a
coding agent, extract short factual notes that would help the agent in future sessions
about this project or this user's preferences.

Focus on:
- Libraries/frameworks the user prefers (e.g. "uses httpx not requests")
- Coding style conventions (e.g. "prefers f-strings", "uses type hints everywhere")
- Project-specific facts (e.g. "tests live in tests/, entry point is src/agent/main.py")
- Explicit preferences or constraints the user stated

Ignore:
- Task-specific details that won't recur
- Things obvious from the code itself
- Conversational filler

Return ONLY valid JSON in this exact shape:
{"memories": [{"key": "short label", "value": "concise fact, 1-2 sentences max"}]}

If there is nothing worth remembering, return: {"memories": []}
"""


def _memory_path(cwd: Path) -> Path:
    d = cwd / ".agent"
    d.mkdir(parents=True, exist_ok=True)
    return d / "memory.json"


def _load_existing(cwd: Path) -> dict[str, str]:
    path = _memory_path(cwd)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(cwd: Path, memories: dict[str, str]) -> None:
    _memory_path(cwd).write_text(
        json.dumps(memories, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_conversation_summary(messages: list[dict[str, Any]], max_turns: int = 10) -> str:
    """Convert the last N user/assistant turns into a readable transcript."""
    relevant = [m for m in messages if m["role"] in ("user", "assistant")]
    relevant = relevant[-max_turns * 2:]  # last N turns (each turn = user + assistant)
    lines: list[str] = []
    for msg in relevant:
        role = msg["role"].upper()
        content = msg.get("content") or ""
        if isinstance(content, list):
            # Block-format system prompt — extract text only
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if isinstance(content, str) and content.strip():
            lines.append(f"{role}: {content[:500]}")
    return "\n\n".join(lines)


async def extract_and_save(
    messages: list[dict[str, Any]],
    cwd: Path,
    cfg: "Config",
) -> dict[str, str]:
    """Extract memorable facts and merge them into the project memory file.

    Returns the updated memory dict (empty dict on any failure).
    """
    from openai import AsyncOpenAI

    from agent.config.config import OPENROUTER_API_KEY

    transcript = _build_conversation_summary(messages)
    if not transcript:
        return {}

    client = AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=OPENROUTER_API_KEY or "no-key",
        default_headers={
            "HTTP-Referer": "https://github.com/my-coding-agent",
            "X-Title": "my-coding-agent",
        },
    )

    try:
        response = await client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Conversation:\n\n{transcript}"},
            ],
            temperature=0,
            max_tokens=512,
        )
        raw = response.choices[0].message.content or ""

        # Extract JSON even if the model wraps it in markdown fences
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        payload = json.loads(match.group())
        new_pairs: list[dict[str, str]] = payload.get("memories", [])
    except Exception:
        return {}

    if not new_pairs:
        return {}

    # Merge into existing memory (new values overwrite same keys)
    existing = _load_existing(cwd)
    for pair in new_pairs:
        key = str(pair.get("key", "")).strip()
        value = str(pair.get("value", "")).strip()
        if key and value:
            existing[key] = value

    _save(cwd, existing)
    return existing


async def extract_from_message(
    user_message: str,
    cfg: "Config",
) -> dict[str, str]:
    """Use DeepSeek to extract key-value memory facts from a single user message.

    Returns a dict of {key: value} pairs (may be empty if nothing to extract).
    """
    from openai import AsyncOpenAI

    from agent.config.config import OPENROUTER_API_KEY

    client = AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=OPENROUTER_API_KEY or "no-key",
        default_headers={
            "HTTP-Referer": "https://github.com/my-coding-agent",
            "X-Title": "my-coding-agent",
        },
    )

    extraction_prompt = (
        "Extract the persistent fact from this message as JSON.\n"
        "Rules:\n"
        "- key: short snake_case label (e.g. user_name, preferred_http_library)\n"
        "- value: the fact, 1 sentence max\n"
        "- Return ONLY valid JSON: {\"key\": \"...\", \"value\": \"...\"}\n"
        "- If multiple facts, return a JSON array: [{\"key\": ..., \"value\": ...}, ...]\n"
        "- If nothing to store, return: {}"
    )

    try:
        response = await client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": extraction_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            max_tokens=150,
        )
        raw = (response.choices[0].message.content or "").strip()

        import re
        # Try to find JSON
        match = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
        if not match:
            return {}
        parsed = json.loads(match.group())

        if isinstance(parsed, dict):
            pairs = [parsed] if parsed.get("key") and parsed.get("value") else []
        elif isinstance(parsed, list):
            pairs = parsed
        else:
            return {}

        return {
            str(p["key"]).strip(): str(p["value"]).strip()
            for p in pairs
            if p.get("key") and p.get("value")
        }
    except Exception:
        return {}


def get_memories(cwd: Path) -> dict[str, str]:
    return _load_existing(cwd)


def delete_memory(cwd: Path, key: str) -> bool:
    """Delete a single key. Returns True if it existed."""
    memories = _load_existing(cwd)
    if key not in memories:
        return False
    del memories[key]
    _save(cwd, memories)
    return True


def clear_memories(cwd: Path) -> None:
    _save(cwd, {})
