"""Integration tests for _SubagentRegistry.

These are the tests that would have caught the schemas_for() missing-override bug:
a unit test for ToolRegistry passes, but the wrapper never reached the model because
_SubagentRegistry only overrode schemas(), not schemas_for().

Each test exercises the full wrapper, not the inner registry in isolation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agent.subagent import MAX_DEPTH, _SubagentRegistry


def _make_inner() -> MagicMock:
    """Minimal ToolRegistry stand-in."""
    m = MagicMock()
    m.schemas.return_value = [{"type": "function", "function": {"name": "read_file"}}]
    m.schemas_for.return_value = [{"type": "function", "function": {"name": "read_file"}}]
    return m


def _make_wrapped(tmp_path: Path, depth: int = 0) -> _SubagentRegistry:
    from agent.config.config import load_config
    cfg = load_config(cwd=tmp_path)
    return _SubagentRegistry(_make_inner(), MagicMock(), cfg, tmp_path, depth=depth)


class TestSubagentRegistrySchemasFor:
    """Regression: schemas_for must inject spawn tools (was missing before)."""

    def test_schemas_for_injects_spawn_subagent(self, tmp_path: Path) -> None:
        wrapped = _make_wrapped(tmp_path)
        names = [s["function"]["name"] for s in wrapped.schemas_for("run tasks in parallel")]
        assert "spawn_subagent" in names

    def test_schemas_for_injects_spawn_parallel(self, tmp_path: Path) -> None:
        wrapped = _make_wrapped(tmp_path)
        names = [s["function"]["name"] for s in wrapped.schemas_for("do something")]
        assert "spawn_parallel" in names

    def test_schemas_injects_both_tools(self, tmp_path: Path) -> None:
        wrapped = _make_wrapped(tmp_path)
        names = [s["function"]["name"] for s in wrapped.schemas()]
        assert "spawn_subagent" in names
        assert "spawn_parallel" in names

    def test_schemas_for_is_superset_of_schemas(self, tmp_path: Path) -> None:
        """schemas_for() must include everything schemas() includes."""
        wrapped = _make_wrapped(tmp_path)
        base_names = {s["function"]["name"] for s in wrapped.schemas()}
        hint_names = {s["function"]["name"] for s in wrapped.schemas_for("anything")}
        assert base_names.issubset(hint_names)

    def test_inner_schemas_for_is_called(self, tmp_path: Path) -> None:
        """schemas_for delegates to the inner registry."""
        inner = _make_inner()
        from agent.config.config import load_config
        cfg = load_config(cwd=tmp_path)
        wrapped = _SubagentRegistry(inner, MagicMock(), cfg, tmp_path, depth=0)
        wrapped.schemas_for("some hint")
        inner.schemas_for.assert_called_once_with("some hint")


class TestSubagentRegistryMaxDepth:
    def test_at_max_depth_spawn_tools_excluded_from_schemas(self, tmp_path: Path) -> None:
        wrapped = _make_wrapped(tmp_path, depth=MAX_DEPTH)
        names = [s["function"]["name"] for s in wrapped.schemas()]
        assert "spawn_subagent" not in names
        assert "spawn_parallel" not in names

    def test_at_max_depth_spawn_tools_excluded_from_schemas_for(self, tmp_path: Path) -> None:
        wrapped = _make_wrapped(tmp_path, depth=MAX_DEPTH)
        names = [s["function"]["name"] for s in wrapped.schemas_for("run parallel tasks")]
        assert "spawn_subagent" not in names
        assert "spawn_parallel" not in names

    def test_below_max_depth_tools_present(self, tmp_path: Path) -> None:
        wrapped = _make_wrapped(tmp_path, depth=MAX_DEPTH - 1)
        names = [s["function"]["name"] for s in wrapped.schemas_for("anything")]
        assert "spawn_subagent" in names
        assert "spawn_parallel" in names


class TestRegistryProtocol:
    def test_tool_registry_satisfies_protocol(self, tmp_path: Path) -> None:
        from agent.config.config import load_config
        from agent.tools.registry import RegistryProtocol, ToolRegistry
        cfg = load_config(cwd=tmp_path)
        registry = ToolRegistry(cfg, tmp_path)
        assert isinstance(registry, RegistryProtocol)

    def test_subagent_registry_satisfies_protocol(self, tmp_path: Path) -> None:
        from agent.tools.registry import RegistryProtocol
        wrapped = _make_wrapped(tmp_path)
        assert isinstance(wrapped, RegistryProtocol)
