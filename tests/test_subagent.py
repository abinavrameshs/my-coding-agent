"""Tests for subagent spawning."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.config.config import Config
from agent.events.bus import Event, EventBus
from agent.events.types import SUBAGENT_END, SUBAGENT_START
from agent.subagent import MAX_DEPTH, _SubagentRegistry, run_subagent


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def _make_mock_registry() -> MagicMock:
    r = MagicMock()
    r.schemas.return_value = []
    r.dispatch = AsyncMock(return_value="<tool_result name=\"x\">\nok\n</tool_result>")
    return r


class TestSubagentRegistry:
    def test_spawn_subagent_schema_included_when_shallow(self, cfg: Config, bus: EventBus) -> None:
        inner = _make_mock_registry()
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=0)
        names = [s["function"]["name"] for s in sr.schemas()]
        assert "spawn_subagent" in names

    def test_spawn_subagent_schema_excluded_at_max_depth(self, cfg: Config, bus: EventBus) -> None:
        inner = _make_mock_registry()
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=MAX_DEPTH)
        names = [s["function"]["name"] for s in sr.schemas()]
        assert "spawn_subagent" not in names

    @pytest.mark.asyncio
    async def test_dispatch_delegates_to_inner(self, cfg: Config, bus: EventBus) -> None:
        inner = _make_mock_registry()
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=0)
        await sr.dispatch("some_tool", {"arg": "val"})
        inner.dispatch.assert_called_once_with("some_tool", {"arg": "val"})

    def test_getattr_delegates_to_inner(self, cfg: Config, bus: EventBus) -> None:
        inner = _make_mock_registry()
        inner.some_attr = "hello"
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=0)
        assert sr.some_attr == "hello"


class TestRunSubagent:
    @pytest.mark.asyncio
    async def test_depth_limit_returns_error(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        result = await run_subagent(
            "do something", None, bus, registry, cfg, depth=MAX_DEPTH, cwd=tmp_path
        )
        assert "depth limit" in result.lower()

    @pytest.mark.asyncio
    async def test_emits_start_and_end_events(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        events: list[str] = []
        bus.on(SUBAGENT_START, lambda e: events.append("start"))
        bus.on(SUBAGENT_END, lambda e: events.append("end"))

        # Mock run_turn so it doesn't make real API calls
        async def fake_run_turn(*args, **kwargs) -> None:
            # Append a minimal assistant message so subagent has something to return
            messages = args[1]
            messages.append({"role": "assistant", "content": "Subagent result here."})

        with patch("agent.loop.run_turn", fake_run_turn), \
             patch("agent.loop.make_client"), \
             patch("agent.memory.loader.assemble_system_prompt", return_value=[{"type": "text", "text": "sys"}]):
            result = await run_subagent(
                "summarise files", None, bus, registry, cfg, depth=0, cwd=tmp_path
            )

        assert "start" in events
        assert "end" in events
        assert "Subagent result" in result

    @pytest.mark.asyncio
    async def test_returns_last_assistant_text(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()

        async def fake_run_turn(*args, **kwargs) -> None:
            messages = args[1]
            messages.append({"role": "assistant", "content": "Final answer from subagent."})

        with patch("agent.loop.run_turn", fake_run_turn), \
             patch("agent.loop.make_client"), \
             patch("agent.memory.loader.assemble_system_prompt", return_value=[{"type": "text", "text": "sys"}]):
            result = await run_subagent(
                "some task", "some context", bus, registry, cfg, depth=0, cwd=tmp_path
            )

        assert result == "Final answer from subagent."
