"""Central tool registry — builds the tool list and dispatches calls.

Tool groups control which schemas are sent to the model:
  core  (always active) — read_file, write_file, edit_file, grep_files, find_files, bash
  web   (opt-in)        — web_search, web_fetch
  git   (opt-in)        — all mcp__git__* tools
  mcp   (opt-in)        — tools from any other configured MCP server

The model can always use bash to run git commands even when the git group is
inactive; the MCP git group just provides more structured alternatives.
"""

from __future__ import annotations

import inspect
from enum import Enum
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


class ToolGroup(str, Enum):
    CORE = "core"   # file tools + bash — always active
    WEB = "web"     # web_search, web_fetch
    GIT = "git"     # mcp__git__* tools
    MCP = "mcp"     # all other MCP server tools


def wrap_tool_result(tool_name: str, content: str) -> str:
    """Wrap tool output to prevent prompt injection."""
    return _INJECTION_WRAPPER.format(name=tool_name, content=content)


class ToolRegistry:
    def __init__(self, cfg: "Config", cwd: Path, mcp: "MCPManager | None" = None) -> None:
        self.cfg = cfg
        self.cwd = cwd
        self.mcp = mcp

        # Start with only core tools; web enabled if configured
        self._active: set[ToolGroup] = {ToolGroup.CORE}
        if cfg.web_search:
            self._active.add(ToolGroup.WEB)

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def enable(self, group: ToolGroup) -> None:
        self._active.add(group)

    def disable(self, group: ToolGroup) -> None:
        self._active.discard(group)

    def active_groups(self) -> set[ToolGroup]:
        return set(self._active)

    # ------------------------------------------------------------------
    # Schema building (what the model sees)
    # ------------------------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        """Return tool schemas for currently active groups only."""
        tools: list[dict[str, Any]] = list(FILE_SCHEMAS) + [BASH_SCHEMA]

        if ToolGroup.WEB in self._active:
            tools.extend(WEB_SCHEMAS)

        if self.mcp:
            mcp_tools = self.mcp.tool_list()
            for t in mcp_tools:
                name: str = t["function"]["name"]
                if name.startswith("mcp__git__"):
                    if ToolGroup.GIT in self._active:
                        tools.append(t)
                else:
                    if ToolGroup.MCP in self._active:
                        tools.append(t)

        return tools

    def tool_count_by_group(self) -> dict[str, int]:
        """Summary of tools per group — used by /tools command."""
        counts: dict[str, int] = {"core": len(FILE_SCHEMAS) + 1}  # +1 for bash
        if self.mcp:
            git_count = sum(
                1 for t in self.mcp.tool_list()
                if t["function"]["name"].startswith("mcp__git__")
            )
            other_count = sum(
                1 for t in self.mcp.tool_list()
                if not t["function"]["name"].startswith("mcp__git__")
            )
            if git_count:
                counts["git"] = git_count
            if other_count:
                counts["mcp"] = other_count
        if self.cfg.web_search:
            counts["web"] = len(WEB_SCHEMAS)
        return counts

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return its output (injection-wrapped)."""
        try:
            raw = await self._run(tool_name, arguments)
        except ToolError as e:
            raw = f"Error: {e}"
        except Exception as e:
            raw = f"Unexpected error in {tool_name}: {e}"
        return wrap_tool_result(tool_name, raw)

    async def _run(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if self.mcp and self.mcp.is_mcp_tool(tool_name):
            return await self.mcp.call(tool_name, arguments, cwd=self.cwd)

        if tool_name in FILE_HANDLERS:
            return FILE_HANDLERS[tool_name](arguments, self.cwd)

        if tool_name == "bash":
            return run_bash(
                arguments["command"],
                cwd=self.cwd,
                timeout=arguments.get("timeout", 30),
                max_output_chars=self.cfg.max_tool_output_chars,
            )

        if tool_name in WEB_HANDLERS:
            handler = WEB_HANDLERS[tool_name]
            if inspect.iscoroutinefunction(handler):
                return await handler(arguments)
            return handler(arguments)

        raise ToolError(f"Unknown tool: {tool_name!r}")
