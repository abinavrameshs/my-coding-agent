"""Plugin tool loader — scans ./agent_tools/*.py for user-defined tools.

Each plugin file must export:
  SCHEMA: dict  — OpenAI-compatible function schema
  async def run(input: dict) -> str  — the tool implementation

A file that is missing either export is logged and skipped.
A plugin that raises during execution returns a ToolError string to the model.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Any

_PLUGIN_DIR_NAME = "agent_tools"


class PluginTool:
    def __init__(self, name: str, schema: dict[str, Any], run_fn: Any, source: str) -> None:
        self.name = name
        self.schema = schema
        self.run_fn = run_fn
        self.source = source

    async def call(self, arguments: dict[str, Any]) -> str:
        try:
            result = await self.run_fn(arguments)
            return str(result)
        except Exception:
            tb = traceback.format_exc(limit=3)
            return f"Plugin error in {self.name}:\n{tb}"


def load_plugins(cwd: Path) -> dict[str, PluginTool]:
    """Scan <cwd>/agent_tools/*.py and return loaded plugins keyed by tool name."""
    plugin_dir = cwd / _PLUGIN_DIR_NAME
    plugins: dict[str, PluginTool] = {}

    if not plugin_dir.exists():
        return plugins

    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        plugin = _load_file(py_file)
        if plugin:
            plugins[plugin.name] = plugin

    return plugins


def _load_file(path: Path) -> PluginTool | None:
    """Load a single plugin file. Return None and print a warning on failure."""
    module_name = f"_agent_plugin_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            print(f"[plugins] Could not create spec for {path.name} — skipped")
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as e:
        print(f"[plugins] Failed to import {path.name}: {e} — skipped")
        return None

    schema = getattr(module, "SCHEMA", None)
    run_fn = getattr(module, "run", None)

    if schema is None:
        print(f"[plugins] {path.name} missing SCHEMA — skipped")
        return None
    if run_fn is None:
        print(f"[plugins] {path.name} missing run() — skipped")
        return None
    if not callable(run_fn):
        print(f"[plugins] {path.name}: run is not callable — skipped")
        return None

    # Ensure the schema is wrapped in {"type": "function", "function": {...}} form
    if "type" not in schema and "function" not in schema:
        schema = {"type": "function", "function": schema}
    tool_name: str = (
        schema.get("function", {}).get("name")
        or schema.get("name")
        or path.stem
    )

    import inspect
    if not inspect.iscoroutinefunction(run_fn):
        # Wrap sync run() in a coroutine
        _sync_run = run_fn
        async def _async_run(input: dict) -> str:  # noqa: E306
            return str(_sync_run(input))
        run_fn = _async_run

    return PluginTool(name=tool_name, schema=schema, run_fn=run_fn, source=str(path))
