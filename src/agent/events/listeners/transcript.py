"""TranscriptListener — writes every event as a JSON line to a per-session JSONL file."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.events.bus import Event, EventBus


class TranscriptListener:
    def __init__(self, session_id: str, cwd: Path) -> None:
        from agent.session import transcripts_dir
        self._path: Path = transcripts_dir(cwd) / f"{session_id}.jsonl"

    def register(self, bus: "EventBus") -> None:
        from agent.events.types import WILDCARD
        bus.on(WILDCARD, self._on_event)

    def _on_event(self, event: "Event") -> None:
        record = {
            "t": time.time(),
            "type": event.type,
            "data": _serialisable(event.data),
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # never crash the agent for transcript errors


def _serialisable(obj: object) -> object:
    """Strip non-serialisable values so json.dumps never raises."""
    from pydantic import BaseModel
    if isinstance(obj, BaseModel):
        return _serialisable(obj.model_dump())
    if isinstance(obj, dict):
        return {k: _serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialisable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)
