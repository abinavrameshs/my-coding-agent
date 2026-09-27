"""Event bus — decouples emitters from listeners throughout the agent.

Usage:
    bus = EventBus()
    bus.on("tool.before", my_handler)      # register
    await bus.emit(Event("tool.before", ToolBeforePayload(tool="bash", input={...})))
    bus.off("tool.before", my_handler)     # unregister

Mutation pattern (tool.before, plan.proposed):
    A listener sets event.data.cancelled = True  to skip execution.
    A listener sets event.data.updated_input = {...} to rewrite the input.
    The agent loop reads these back after await bus.emit(...).

    Payload fields are mutable — Pydantic models are not frozen.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

from agent.events.payloads import EmptyPayload
from agent.events.types import WILDCARD

Handler = Callable[["Event"], Union[None, Awaitable[None]]]


@dataclass
class Event:
    type: str
    # `data` is typed as Any so handlers can access payload attributes without
    # casts. Type safety is enforced at the emitter: passing a typed Pydantic
    # payload model (from events/payloads.py) raises ValidationError on a
    # wrong field name before the event ever reaches the bus.
    data: Any = field(default_factory=EmptyPayload)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def on(self, event_type: str, handler: Handler) -> None:
        """Register *handler* for *event_type* (or WILDCARD for all events)."""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: Handler) -> None:
        """Remove a previously registered handler. Silent if not found."""
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    async def emit(self, event: Event) -> Event:
        """Call all handlers for *event.type* then all wildcard handlers.

        Handlers run sequentially in registration order so that mutation
        (cancelled / updated_input) is predictable — each handler sees
        changes made by the previous one.

        Returns the event so callers can inspect mutations inline:
            event = await bus.emit(Event(TOOL_BEFORE, ToolBeforePayload(tool="bash")))
            if event.data.cancelled: ...
        """
        for handler in list(self._handlers.get(event.type, [])):
            await self._call(handler, event)

        if event.type != WILDCARD:
            for handler in list(self._handlers.get(WILDCARD, [])):
                await self._call(handler, event)

        return event

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _call(handler: Handler, event: Event) -> None:
        result = handler(event)
        if inspect.isawaitable(result):
            await result
