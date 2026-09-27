"""Tests for the plugin tool loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.tools.plugins import load_plugins


def _write_plugin(plugin_dir: Path, name: str, code: str) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    f = plugin_dir / f"{name}.py"
    f.write_text(textwrap.dedent(code))
    return f


class TestLoadPlugins:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        plugins = load_plugins(tmp_path)
        assert plugins == {}

    def test_no_plugin_dir_returns_empty(self, tmp_path: Path) -> None:
        plugins = load_plugins(tmp_path)
        assert plugins == {}

    def test_valid_async_plugin_loaded(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "agent_tools", "echo", """
            SCHEMA = {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                }
            }
            async def run(input):
                return input["text"]
        """)
        plugins = load_plugins(tmp_path)
        assert "echo" in plugins

    def test_valid_sync_plugin_wrapped_as_async(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "agent_tools", "double", """
            SCHEMA = {
                "type": "function",
                "function": {
                    "name": "double",
                    "description": "Double",
                    "parameters": {
                        "type": "object",
                        "properties": {"n": {"type": "integer"}},
                        "required": ["n"],
                        "additionalProperties": False,
                    },
                }
            }
            def run(input):
                return str(input["n"] * 2)
        """)
        plugins = load_plugins(tmp_path)
        assert "double" in plugins

    def test_missing_schema_skips_plugin(self, tmp_path: Path, capsys) -> None:
        _write_plugin(tmp_path / "agent_tools", "noschema", """
            async def run(input):
                return "ok"
        """)
        plugins = load_plugins(tmp_path)
        assert "noschema" not in plugins

    def test_missing_run_skips_plugin(self, tmp_path: Path, capsys) -> None:
        _write_plugin(tmp_path / "agent_tools", "norun", """
            SCHEMA = {"type": "function", "function": {"name": "norun", "description": "", "parameters": {}}}
        """)
        plugins = load_plugins(tmp_path)
        assert "norun" not in plugins

    def test_syntax_error_skips_plugin(self, tmp_path: Path, capsys) -> None:
        _write_plugin(tmp_path / "agent_tools", "broken", "this is not python !!!")
        plugins = load_plugins(tmp_path)
        assert "broken" not in plugins

    def test_underscore_files_ignored(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "agent_tools", "__helper", """
            SCHEMA = {"type": "function", "function": {"name": "__helper", "description": "", "parameters": {}}}
            async def run(input): return "x"
        """)
        plugins = load_plugins(tmp_path)
        assert "__helper" not in plugins


class TestPluginToolCall:
    @pytest.mark.asyncio
    async def test_async_plugin_returns_result(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "agent_tools", "greet", """
            SCHEMA = {"type": "function", "function": {"name": "greet", "description": "", "parameters": {}}}
            async def run(input):
                return "Hello, world!"
        """)
        plugins = load_plugins(tmp_path)
        result = await plugins["greet"].call({})
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_plugin_exception_returns_error_string(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path / "agent_tools", "crasher", """
            SCHEMA = {"type": "function", "function": {"name": "crasher", "description": "", "parameters": {}}}
            async def run(input):
                raise ValueError("intentional error")
        """)
        plugins = load_plugins(tmp_path)
        result = await plugins["crasher"].call({})
        assert "Plugin error" in result
        assert "intentional error" in result
