"""AutoMemoryListener — extracts project facts at session end and saves them.

On SESSION_END:
1. Asks JEV whether the session is worth extracting memory from (skips short sessions).
2. If yes, calls DeepSeek to extract key-value facts from the conversation.
3. Merges new facts into ~/.agent/memory/<project_hash>.json.
4. Prints a summary of what was saved.

On the next session, the loader injects these facts into the system prompt as
"## Remembered context".
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.events.bus import Event, EventBus

console = Console()


class AutoMemoryListener:
    def __init__(self, cfg: "Config", cwd: Path, messages: list[dict[str, Any]]) -> None:
        self._cfg = cfg
        self._cwd = cwd
        self._messages = messages
        self._turn_count = 0

    def register(self, bus: "EventBus") -> None:
        from agent.events.types import SESSION_END, TURN_END
        bus.on(TURN_END, self._on_turn_end)
        bus.on(SESSION_END, self._on_session_end)

    def _on_turn_end(self, _event: "Event") -> None:
        self._turn_count += 1

    def _on_session_end(self, _event: "Event") -> None:
        if not self._cfg.auto_memory:
            return
        # Run extraction in a new event loop slice so we don't block session teardown
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._extract())
            else:
                loop.run_until_complete(self._extract())
        except Exception:
            pass

    async def _extract(self) -> None:
        from agent.memory.auto import extract_and_save
        from agent.routing import should_extract_memory

        last_user = next(
            (m["content"] for m in reversed(self._messages)
             if m["role"] == "user" and isinstance(m.get("content"), str)),
            "",
        )

        try:
            worth_it = await asyncio.wait_for(
                should_extract_memory(self._turn_count, last_user, self._cfg),
                timeout=self._cfg.jev_timeout,
            )
        except Exception:
            worth_it = self._turn_count >= 3  # fallback heuristic

        if not worth_it:
            return

        try:
            saved = await asyncio.wait_for(
                extract_and_save(self._messages, self._cwd, self._cfg),
                timeout=30.0,
            )
        except Exception:
            return

        if saved:
            console.print(f"\n[dim]💾 Remembered {len(saved)} fact(s) for this project[/dim]")
