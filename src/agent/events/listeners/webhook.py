"""WebhookListener — POSTs selected events to a configured URL (fire-and-forget).

Config:
  cfg.webhook_url    — destination URL; None disables the listener
  cfg.webhook_events — list of event type strings to forward; empty = all events

Delivery failures are logged but never crash the agent.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.events.bus import Event, EventBus


def _serialisable(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _serialisable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialisable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


class WebhookListener:
    def __init__(self, cfg: "Config") -> None:
        self._url: str | None = cfg.webhook_url
        self._events: set[str] = set(cfg.webhook_events)  # empty = all

    def register(self, bus: "EventBus") -> None:
        if not self._url:
            return
        from agent.events.types import WILDCARD
        bus.on(WILDCARD, self._on_event)

    def _on_event(self, event: "Event") -> None:
        if self._events and event.type not in self._events:
            return
        payload = {
            "t": time.time(),
            "type": event.type,
            "data": _serialisable(event.data),
        }
        asyncio.create_task(self._post(payload))

    async def _post(self, payload: dict[str, Any]) -> None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    self._url,  # type: ignore[arg-type]
                    content=json.dumps(payload, ensure_ascii=False),
                    headers={"Content-Type": "application/json"},
                )
        except Exception:
            # Delivery failure must not crash the agent
            pass
