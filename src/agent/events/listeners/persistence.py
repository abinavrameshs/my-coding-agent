"""PersistenceListener — saves message history on every turn so sessions can be resumed."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.events.bus import Event, EventBus


class PersistenceListener:
    def __init__(self, session_id: str, messages: list[dict], cwd: Path) -> None:
        from agent.session import sessions_dir
        self._path: Path = sessions_dir(cwd) / f"{session_id}.json"
        self._messages = messages

    def register(self, bus: "EventBus") -> None:
        from agent.events.types import TURN_END
        bus.on(TURN_END, self._on_turn_end)

    def _on_turn_end(self, event: "Event") -> None:
        meta = {
            "session_id": self._path.stem,
            "saved_at": time.time(),
            "turn_count": sum(1 for m in self._messages if m["role"] == "user"),
        }
        try:
            payload = {"meta": meta, "messages": self._messages}
            self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def load_session(session_id: str, cwd: Path) -> list[dict] | None:
    """Return the saved messages for *session_id*, or None if not found."""
    from agent.session import sessions_dir
    path = sessions_dir(cwd) / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("messages", [])
    except (OSError, json.JSONDecodeError):
        return None


def list_sessions(cwd: Path, limit: int = 10) -> list[dict]:
    """Return the most recent sessions sorted newest-first."""
    from agent.session import sessions_dir
    sessions = []
    for p in sessions_dir(cwd).glob("*.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            meta = payload.get("meta", {})
            msgs = payload.get("messages", [])
            first_user = next(
                (m["content"] for m in msgs if m["role"] == "user" and isinstance(m["content"], str)),
                "",
            )
            sessions.append({
                "id": p.stem,
                "saved_at": meta.get("saved_at", 0),
                "turns": meta.get("turn_count", 0),
                "first_message": first_user[:80],
            })
        except (OSError, json.JSONDecodeError):
            continue
    sessions.sort(key=lambda s: s["saved_at"], reverse=True)
    return sessions[:limit]
