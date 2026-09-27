"""Tests for subagent spawning."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.config.config import Config
from agent.events.bus import EventBus
from agent.events.types import (
    PARALLEL_BATCH_END,
    PARALLEL_BATCH_START,
    SUBAGENT_END,
    SUBAGENT_START,
)
from agent.subagent import (
    MAX_DEPTH,
    _SubagentRegistry,
    run_parallel,
    run_subagent,
)


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def _make_mock_registry() -> MagicMock:
    r = MagicMock()
    r.schemas.return_value = []
    r.schemas_for.return_value = []
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

    def test_schemas_for_includes_subagent_tools_when_shallow(
        self, cfg: Config, bus: EventBus
    ) -> None:
        # Regression: the loop calls schemas_for(hint); the subagent tools must
        # be injected there too, not just in schemas().
        inner = _make_mock_registry()
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=0)
        names = [s["function"]["name"] for s in sr.schemas_for("do something")]
        assert "spawn_subagent" in names
        assert "spawn_parallel" in names

    def test_schemas_for_excludes_subagent_tools_at_max_depth(
        self, cfg: Config, bus: EventBus
    ) -> None:
        inner = _make_mock_registry()
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=MAX_DEPTH)
        names = [s["function"]["name"] for s in sr.schemas_for("do something")]
        assert "spawn_subagent" not in names
        assert "spawn_parallel" not in names

    @pytest.mark.asyncio
    async def test_dispatch_parallel_delegates(self, cfg: Config, bus: EventBus) -> None:
        inner = _make_mock_registry()
        sr = _SubagentRegistry(inner, bus, cfg, Path("."), depth=0)

        async def fake_run_parallel(*args, **kwargs) -> str:
            return "### Task 1\nok"

        with patch("agent.subagent.run_parallel", fake_run_parallel):
            out = await sr.dispatch("spawn_parallel", {"tasks": [{"id": "1", "prompt": "x"}]})
        assert "spawn_parallel" in out
        assert "ok" in out

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


class TestRunParallel:
    @pytest.mark.asyncio
    async def test_runs_all_tasks_and_aggregates(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        calls: list[str] = []

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            calls.append(prompt)
            return f"result for {prompt}"

        tasks = [
            {"id": "1", "prompt": "alpha"},
            {"id": "2", "prompt": "beta"},
        ]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            out = await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert sorted(calls) == ["alpha", "beta"]
        assert "### Task 1" in out
        assert "### Task 2" in out
        assert "result for alpha" in out
        assert "result for beta" in out

    @pytest.mark.asyncio
    async def test_independent_tasks_run_concurrently(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        import asyncio

        registry = _make_mock_registry()
        active = 0
        peak = 0

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return f"done {prompt}"

        tasks = [{"id": str(i), "prompt": f"t{i}"} for i in range(3)]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert peak == 3  # all three overlapped

    @pytest.mark.asyncio
    async def test_width_cap_limits_concurrency(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        import asyncio

        cfg = cfg.model_copy(update={"max_parallel_subagents": 2})
        registry = _make_mock_registry()
        active = 0
        peak = 0

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return "ok"

        tasks = [{"id": str(i), "prompt": f"t{i}"} for i in range(5)]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert peak <= 2

    @pytest.mark.asyncio
    async def test_dependencies_serialise(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        order: list[str] = []

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            order.append(prompt)
            return prompt

        tasks = [{"id": "1", "prompt": "first"}, {"id": "2", "prompt": "second"}]
        deps = {"2": ["1"]}
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            await run_parallel(tasks, deps, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_overlapping_files_serialise(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        import asyncio

        registry = _make_mock_registry()
        active = 0
        peak = 0

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return "ok"

        tasks = [
            {"id": "1", "prompt": "a", "files": ["shared.py"]},
            {"id": "2", "prompt": "b", "files": ["shared.py"]},
        ]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert peak == 1

    @pytest.mark.asyncio
    async def test_parallel_planning_disabled_serialises(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        import asyncio

        cfg = cfg.model_copy(update={"parallel_planning": False})
        registry = _make_mock_registry()
        active = 0
        peak = 0

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return "ok"

        tasks = [{"id": str(i), "prompt": f"t{i}"} for i in range(4)]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert peak == 1

    @pytest.mark.asyncio
    async def test_emits_batch_events(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        events: list[str] = []
        bus.on(PARALLEL_BATCH_START, lambda e: events.append("start"))
        bus.on(PARALLEL_BATCH_END, lambda e: events.append("end"))

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            return "ok"

        tasks = [{"id": "1", "prompt": "a"}, {"id": "2", "prompt": "b"}]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert events.count("start") == 1
        assert events.count("end") == 1

    @pytest.mark.asyncio
    async def test_depth_limit_returns_error(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        out = await run_parallel(
            [{"id": "1", "prompt": "x"}], None, bus, registry, cfg,
            depth=MAX_DEPTH, cwd=tmp_path,
        )
        assert "depth limit" in out.lower()

    @pytest.mark.asyncio
    async def test_empty_tasks_returns_error(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        out = await run_parallel([], None, bus, registry, cfg, depth=0, cwd=tmp_path)
        assert "no valid tasks" in out.lower()

    @pytest.mark.asyncio
    async def test_malformed_tasks_skipped(
        self, cfg: Config, bus: EventBus, tmp_path: Path
    ) -> None:
        registry = _make_mock_registry()
        seen: list[str] = []

        async def fake_run_subagent(prompt, context, *args, **kwargs) -> str:
            seen.append(prompt)
            return "ok"

        tasks = [
            {"id": "1", "prompt": "good"},
            {"prompt": "no id"},
            {"id": "2", "prompt": ""},        # empty prompt -> skipped
            "not-a-dict",
            {"id": "1", "prompt": "dupe"},    # duplicate id -> skipped
        ]
        with patch("agent.subagent.run_subagent", fake_run_subagent):
            out = await run_parallel(tasks, None, bus, registry, cfg, depth=0, cwd=tmp_path)

        assert seen == ["good"]
        assert "### Task 1" in out
        assert "Task 2" not in out

