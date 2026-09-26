"""Tests for the EventBus."""

import asyncio

import pytest

from agent.events.bus import Event, EventBus
from agent.events.types import TOOL_BEFORE, WILDCARD


class TestRegistrationAndEmission:
    async def test_sync_handler_called(self) -> None:
        bus = EventBus()
        calls: list[str] = []
        bus.on("turn.start", lambda e: calls.append(e.type))
        await bus.emit(Event("turn.start"))
        assert calls == ["turn.start"]

    async def test_async_handler_awaited(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        async def handler(e: Event) -> None:
            calls.append(e.type)

        bus.on("turn.start", handler)
        await bus.emit(Event("turn.start"))
        assert calls == ["turn.start"]

    async def test_multiple_handlers_called_in_order(self) -> None:
        bus = EventBus()
        order: list[int] = []
        bus.on("x", lambda e: order.append(1))
        bus.on("x", lambda e: order.append(2))
        bus.on("x", lambda e: order.append(3))
        await bus.emit(Event("x"))
        assert order == [1, 2, 3]

    async def test_unregistered_event_no_error(self) -> None:
        bus = EventBus()
        await bus.emit(Event("unknown.event"))  # must not raise

    async def test_emit_returns_event(self) -> None:
        bus = EventBus()
        event = Event("foo")
        result = await bus.emit(event)
        assert result is event


class TestWildcard:
    async def test_wildcard_receives_all_events(self) -> None:
        bus = EventBus()
        seen: list[str] = []
        bus.on(WILDCARD, lambda e: seen.append(e.type))
        await bus.emit(Event("turn.start"))
        await bus.emit(Event("turn.end"))
        assert seen == ["turn.start", "turn.end"]

    async def test_wildcard_fires_after_specific_handler(self) -> None:
        bus = EventBus()
        order: list[str] = []
        bus.on("x", lambda e: order.append("specific"))
        bus.on(WILDCARD, lambda e: order.append("wildcard"))
        await bus.emit(Event("x"))
        assert order == ["specific", "wildcard"]

    async def test_wildcard_event_itself_not_double_fired(self) -> None:
        """Emitting WILDCARD directly should not re-invoke wildcard handlers."""
        bus = EventBus()
        calls: list[int] = []
        bus.on(WILDCARD, lambda e: calls.append(1))
        await bus.emit(Event(WILDCARD))
        assert calls == [1]  # exactly once


class TestOff:
    async def test_off_removes_handler(self) -> None:
        bus = EventBus()
        calls: list[int] = []
        handler = lambda e: calls.append(1)  # noqa: E731
        bus.on("x", handler)
        bus.off("x", handler)
        await bus.emit(Event("x"))
        assert calls == []

    async def test_off_unknown_handler_silent(self) -> None:
        bus = EventBus()
        bus.off("x", lambda e: None)  # must not raise

    async def test_off_only_removes_one_registration(self) -> None:
        bus = EventBus()
        calls: list[int] = []
        h1 = lambda e: calls.append(1)  # noqa: E731
        h2 = lambda e: calls.append(2)  # noqa: E731
        bus.on("x", h1)
        bus.on("x", h2)
        bus.off("x", h1)
        await bus.emit(Event("x"))
        assert calls == [2]


class TestMutation:
    async def test_listener_can_cancel_event(self) -> None:
        bus = EventBus()
        bus.on(TOOL_BEFORE, lambda e: e.data.update({"cancelled": True}))
        event = await bus.emit(Event(TOOL_BEFORE, {"tool": "bash"}))
        assert event.data["cancelled"] is True

    async def test_listener_can_rewrite_input(self) -> None:
        bus = EventBus()

        def rewrite(e: Event) -> None:
            e.data["updated_input"] = {"command": "echo safe"}

        bus.on(TOOL_BEFORE, rewrite)
        event = await bus.emit(Event(TOOL_BEFORE, {"tool": "bash", "input": {"command": "rm -rf /"}}))
        assert event.data["updated_input"] == {"command": "echo safe"}

    async def test_later_handler_sees_earlier_mutation(self) -> None:
        bus = EventBus()
        bus.on("x", lambda e: e.data.update({"v": 1}))
        bus.on("x", lambda e: e.data.update({"v": e.data["v"] + 1}))
        event = await bus.emit(Event("x", {"v": 0}))
        assert event.data["v"] == 2

    async def test_event_data_accessible_after_emit(self) -> None:
        bus = EventBus()
        bus.on("x", lambda e: e.data.update({"result": 42}))
        event = Event("x")
        await bus.emit(event)
        assert event.data["result"] == 42
