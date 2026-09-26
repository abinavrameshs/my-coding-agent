"""MCP server client — runs one stdio MCP server in a dedicated background task.

The stdio_client context manager uses anyio cancel scopes that must be entered
and exited in the same task. To avoid that constraint, each MCPClient owns a
long-lived background task that holds the context open for its entire lifetime.
Tool calls are dispatched via asyncio.Queue so any task can call them safely.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class _Call:
    tool: str          # bare tool name (without namespace prefix)
    arguments: dict[str, Any]
    result: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class MCPClient:
    """Manages a single MCP server subprocess in a background task."""

    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.env = env or {}
        self._tools: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[_Call | None] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._error: Exception | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background task and wait until the server is initialised."""
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        await self._ready.wait()
        if self._error:
            raise self._error

    async def _run(self) -> None:
        """Long-lived background task — holds the stdio_client context open."""
        params = StdioServerParameters(
            command=self.command[0],
            args=self.command[1:],
            env=self.env or None,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await self._load_tools(session)
                    self._ready.set()          # signal start() to unblock

                    # Serve tool calls until stop() sends a None sentinel
                    while True:
                        call = await self._queue.get()
                        if call is None:
                            break
                        try:
                            result = await session.call_tool(call.tool, call.arguments)
                            parts = [c.text for c in result.content if hasattr(c, "text")]
                            call.result.set_result("\n".join(parts))
                        except Exception as exc:
                            call.result.set_exception(exc)
        except Exception as exc:
            self._error = exc
            self._ready.set()              # unblock start() so it can raise

    async def _load_tools(self, session: ClientSession) -> None:
        result = await session.list_tools()
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": f"mcp__{self.name}__{t.name}",
                    "description": t.description or "",
                    "parameters": (
                        t.input_schema
                        if isinstance(t.input_schema, dict)
                        else {}
                    ),
                },
            }
            for t in result.tools
        ]

    async def call(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call; blocks until the background task returns a result."""
        bare = namespaced_name.removeprefix(f"mcp__{self.name}__")
        pending = _Call(tool=bare, arguments=arguments)
        await self._queue.put(pending)
        return await pending.result

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def stop(self) -> None:
        """Signal the background task to exit and wait for it to finish."""
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
