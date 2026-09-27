"""Central tool registry — builds the tool list and dispatches calls.

Tool groups control which schemas are sent to the model:
  core    (always active) — read_file, write_file, edit_file, grep_files, find_files, bash
  web     (opt-in)        — web_search, web_fetch
  git     (opt-in)        — all mcp__git__* tools
  mcp     (opt-in)        — tools from any other configured MCP server
  plugin  (auto-loaded)   — tools from ./agent_tools/*.py

Schema caching: the full schema list is built once per session and cached.
  Call invalidate_schema_cache() after adding/removing MCP servers.

MCP relevance filtering: schemas_for(hint) scores each MCP server against the
  current user message and excludes zero-scoring servers.  Core tools, plugin
  tools, and any server that has been called this session are always included.
"""

from __future__ import annotations

import inspect
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agent.tools.bash import SCHEMA as BASH_SCHEMA
from agent.tools.bash import run_bash
from agent.tools.files import HANDLERS as FILE_HANDLERS
from agent.tools.files import SCHEMAS as FILE_SCHEMAS
from agent.tools.files import ToolError
from agent.tools.memory_tools import HANDLERS as MEMORY_HANDLERS
from agent.tools.memory_tools import SCHEMAS as MEMORY_SCHEMAS
from agent.tools.todo import HANDLERS as TODO_HANDLERS
from agent.tools.todo import SCHEMAS as TODO_SCHEMAS
from agent.tools.web import HANDLERS as WEB_HANDLERS
from agent.tools.web import SCHEMAS as WEB_SCHEMAS

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.mcp.manager import MCPManager

_INJECTION_WRAPPER = '<tool_result name="{name}">\n{content}\n</tool_result>'


@runtime_checkable
class RegistryProtocol(Protocol):
    """Structural interface that both ToolRegistry and _SubagentRegistry satisfy.

    ``run_turn`` accepts any object matching this protocol, so mypy catches missing
    method implementations on wrapper classes at type-check time rather than
    at runtime.
    """

    def schemas(self) -> list[dict[str, Any]]: ...
    def schemas_for(self, hint: str) -> list[dict[str, Any]]: ...
    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str: ...


class ToolGroup(str, Enum):
    CORE = "core"   # file tools + bash — always active
    WEB = "web"     # web_search, web_fetch
    GIT = "git"     # mcp__git__* tools
    MCP = "mcp"     # all other MCP server tools


def wrap_tool_result(tool_name: str, content: str) -> str:
    """Wrap tool output to prevent prompt injection."""
    return _INJECTION_WRAPPER.format(name=tool_name, content=content)


_STOP_WORDS = frozenset(
    "the and for that with this from are has been can will you your its not"
    " how what when where which who why does did get set use let all any".split()
)


def _keywords(text: str) -> set[str]:
    """Extract meaningful words from a string."""
    return {w for w in re.findall(r"\b[a-z]{3,}\b", text.lower()) if w not in _STOP_WORDS}


def _server_name(tool_name: str) -> str:
    """mcp__github__create_issue → 'github'"""
    parts = tool_name.split("__", 2)
    return parts[1] if len(parts) >= 2 else ""


class ToolRegistry:
    def __init__(self, cfg: "Config", cwd: Path, mcp: "MCPManager | None" = None) -> None:
        self.cfg = cfg
        self.cwd = cwd
        self.mcp = mcp

        # Start with only core tools; web enabled if configured
        self._active: set[ToolGroup] = {ToolGroup.CORE}
        if cfg.web_search:
            self._active.add(ToolGroup.WEB)

        # Load plugin tools from ./agent_tools/
        from agent.tools.plugins import load_plugins
        self._plugins = load_plugins(cwd)

        # Schema cache — built once, invalidated on MCP changes
        self._schema_cache: list[dict[str, Any]] | None = None

        # Per-server keyword sets for relevance filtering (built lazily)
        self._server_keywords: dict[str, set[str]] = {}

        # Servers called this session — always included regardless of hint
        self._servers_used: set[str] = set()

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def enable(self, group: ToolGroup) -> None:
        self._active.add(group)
        self.invalidate_schema_cache()

    def disable(self, group: ToolGroup) -> None:
        self._active.discard(group)
        self.invalidate_schema_cache()

    def active_groups(self) -> set[ToolGroup]:
        return set(self._active)

    def invalidate_schema_cache(self) -> None:
        self._schema_cache = None
        self._server_keywords = {}

    # ------------------------------------------------------------------
    # Schema building (what the model sees)
    # ------------------------------------------------------------------

    def _base_tools(self) -> list[dict[str, Any]]:
        """Core + web + plugin schemas — never filtered."""
        tools: list[dict[str, Any]] = (
            list(FILE_SCHEMAS) + [BASH_SCHEMA] + list(TODO_SCHEMAS) + list(MEMORY_SCHEMAS)
        )
        if ToolGroup.WEB in self._active:
            tools.extend(WEB_SCHEMAS)
        for plugin in self._plugins.values():
            tools.append(plugin.schema)
        return tools

    def _mcp_by_server(self) -> dict[str, list[dict[str, Any]]]:
        """Group active MCP tool schemas by server name."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        if not self.mcp:
            return grouped
        for t in self.mcp.tool_list():
            name: str = t["function"]["name"]
            is_git = name.startswith("mcp__git__")
            if is_git and ToolGroup.GIT not in self._active:
                continue
            if not is_git and ToolGroup.MCP not in self._active:
                continue
            server = _server_name(name)
            grouped.setdefault(server, []).append(t)
        return grouped

    def _build_server_keywords(self) -> dict[str, set[str]]:
        if self._server_keywords:
            return self._server_keywords
        for server, tools in self._mcp_by_server().items():
            words: set[str] = _keywords(server)
            for t in tools:
                fn = t["function"]
                words |= _keywords(fn.get("name", ""))
                words |= _keywords(fn.get("description", ""))
            self._server_keywords[server] = words
        return self._server_keywords

    def schemas(self) -> list[dict[str, Any]]:
        """Return all active tool schemas. Result is cached for the session."""
        if self._schema_cache is not None:
            return self._schema_cache
        tools = self._base_tools()
        for server_tools in self._mcp_by_server().values():
            tools.extend(server_tools)
        self._schema_cache = tools
        return tools

    def schemas_for(self, hint: str) -> list[dict[str, Any]]:
        """Return active schemas filtered by relevance to *hint* (the user message).

        MCP servers with zero keyword overlap with the hint are excluded — unless:
          - The hint is too short to be reliable (< 6 words)
          - All servers score zero (fallback: include all to avoid cutting off everything)
          - The server was already called this session
        Core, web, and plugin tools are always included.
        """
        hint_words = _keywords(hint)
        by_server = self._mcp_by_server()

        # Not enough signal — return full cached list
        if len(hint_words) < 6 or not by_server:
            return self.schemas()

        server_kw = self._build_server_keywords()
        scores = {
            server: len(server_kw.get(server, set()) & hint_words)
            for server in by_server
        }

        # If every server scores 0, include all (no signal to act on)
        if all(s == 0 for s in scores.values()):
            return self.schemas()

        tools = self._base_tools()
        for server, server_tools in by_server.items():
            if scores.get(server, 0) > 0 or server in self._servers_used:
                tools.extend(server_tools)

        return tools

    def tool_count_by_group(self) -> dict[str, int]:
        """Summary of tools per group — used by /tools command."""
        counts: dict[str, int] = {"core": len(FILE_SCHEMAS) + 1 + len(TODO_SCHEMAS) + len(MEMORY_SCHEMAS)}
        if self._plugins:
            counts["plugins"] = len(self._plugins)
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
            self._servers_used.add(_server_name(tool_name))
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

        if tool_name in TODO_HANDLERS:
            return TODO_HANDLERS[tool_name](arguments, self.cwd)

        if tool_name in MEMORY_HANDLERS:
            return MEMORY_HANDLERS[tool_name](arguments, self.cwd)

        if tool_name in WEB_HANDLERS:
            handler = WEB_HANDLERS[tool_name]
            if inspect.iscoroutinefunction(handler):
                return await handler(arguments)
            return handler(arguments)

        if tool_name in self._plugins:
            return await self._plugins[tool_name].call(arguments)

        raise ToolError(f"Unknown tool: {tool_name!r}")
