"""MCP server client — wraps one stdio MCP server subprocess."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    """Manages a single MCP server subprocess."""

    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.env = env or {}
        self._session: ClientSession | None = None
        self._stack = AsyncExitStack()
        self._tools: list[dict[str, Any]] = []

    async def start(self) -> None:
        params = StdioServerParameters(
            command=self.command[0],
            args=self.command[1:],
            env=self.env or None,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        await self._refresh_tools()

    async def _refresh_tools(self) -> None:
        if not self._session:
            return
        result = await self._session.list_tools()
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": f"mcp__{self.name}__{t.name}",
                    "description": t.description or "",
                    "parameters": t.inputSchema if isinstance(t.inputSchema, dict) else {},
                },
            }
            for t in result.tools
        ]

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by its namespaced name (mcp__<server>__<tool>)."""
        if not self._session:
            raise RuntimeError(f"MCP server '{self.name}' is not started")
        raw = tool_name.removeprefix(f"mcp__{self.name}__")
        result = await self._session.call_tool(raw, arguments)
        parts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(parts)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def stop(self) -> None:
        await self._stack.aclose()
        self._session = None
