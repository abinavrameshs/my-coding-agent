"""Central tool registry — builds the tool list and dispatches calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.tools.bash import SCHEMA as BASH_SCHEMA
from agent.tools.bash import run_bash
from agent.tools.files import HANDLERS as FILE_HANDLERS
from agent.tools.files import SCHEMAS as FILE_SCHEMAS
from agent.tools.files import ToolError
from agent.tools.web import HANDLERS as WEB_HANDLERS
from agent.tools.web import SCHEMAS as WEB_SCHEMAS

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.mcp.manager import MCPManager

_INJECTION_WRAPPER = '<tool_result name="{name}">\n{content}\n</tool_result>'


def wrap_tool_result(tool_name: str, content: str) -> str:
    """Wrap tool output to prevent prompt injection."""
    return _INJECTION_WRAPPER.format(name=tool_name, content=content)


class ToolRegistry:
    def __init__(self, cfg: "Config", cwd: Path, mcp: "MCPManager | None" = None) -> None:
        self.cfg = cfg
        self.cwd = cwd
        self.mcp = mcp

    def schemas(self) -> list[dict[str, Any]]:
        """Return all tool schemas in OpenAI format."""
        tools = list(FILE_SCHEMAS) + [BASH_SCHEMA]
        if self.cfg.web_search:
            tools.extend(WEB_SCHEMAS)
        if self.mcp:
            tools.extend(self.mcp.tool_list())
        return tools

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return its output (already injection-wrapped)."""
        try:
            raw = await self._run(tool_name, arguments)
        except ToolError as e:
            raw = f"Error: {e}"
        except Exception as e:
            raw = f"Unexpected error in {tool_name}: {e}"
        return wrap_tool_result(tool_name, raw)

    async def _run(self, tool_name: str, arguments: dict[str, Any]) -> str:
        # MCP tools
        if self.mcp and self.mcp.is_mcp_tool(tool_name):
            return await self.mcp.call(tool_name, arguments, cwd=self.cwd)

        # File tools
        if tool_name in FILE_HANDLERS:
            return FILE_HANDLERS[tool_name](arguments, self.cwd)

        # Bash
        if tool_name == "bash":
            return run_bash(
                arguments["command"],
                cwd=self.cwd,
                timeout=arguments.get("timeout", 30),
                max_output_chars=self.cfg.max_tool_output_chars,
            )

        # Web tools
        if tool_name in WEB_HANDLERS:
            handler = WEB_HANDLERS[tool_name]
            import inspect
            if inspect.iscoroutinefunction(handler):
                return await handler(arguments)
            return handler(arguments)

        raise ToolError(f"Unknown tool: {tool_name!r}")
