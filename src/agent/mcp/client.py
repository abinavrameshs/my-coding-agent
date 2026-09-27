"""MCP transport clients — stdio and HTTP (Streamable HTTP / SSE).

StdioMCPClient: runs a local subprocess, communicates over stdin/stdout.
HttpMCPClient:  connects to a remote HTTP MCP server (Streamable HTTP or SSE).

Both expose the same interface: start(), stop(), call(), .tools, .name.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


def _unwrap(exc: BaseException) -> Exception:
    """Unwrap Python 3.11+ ExceptionGroup to its first leaf for a readable message."""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            return _unwrap(sub)
    return exc if isinstance(exc, Exception) else RuntimeError(str(exc))


@dataclass
class _Call:
    tool: str
    arguments: dict[str, Any]
    result: asyncio.Future = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )


class StdioMCPClient:
    """Manages a single MCP server subprocess in a background task."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._tools: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[_Call | None] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._error: Exception | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        await self._ready.wait()
        if self._error:
            raise self._error

    async def _run(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env or None,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await self._load_tools(session)
                    self._ready.set()
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
            self._ready.set()

    async def _load_tools(self, session: Any) -> None:
        result = await session.list_tools()
        self._tools = _format_tools(self.name, result.tools)

    async def call(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        bare = namespaced_name.removeprefix(f"mcp__{self.name}__")
        pending = _Call(tool=bare, arguments=arguments)
        await self._queue.put(pending)
        return await pending.result

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def stop(self) -> None:
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()


class HttpMCPClient:
    """Connects to a remote HTTP MCP server (Streamable HTTP or SSE fallback)."""

    def __init__(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.headers = headers or {}
        self._tools: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[_Call | None] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._error: Exception | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp-http-{self.name}")
        await self._ready.wait()
        if self._error:
            raise self._error

    async def _run(self) -> None:
        # Try Streamable HTTP first (MCP 2.x standard), fall back to SSE
        try:
            await self._run_streamable_http()
        except Exception as exc:
            try:
                await self._run_sse()
            except Exception:
                self._error = _unwrap(exc)
                self._ready.set()

    async def _run_streamable_http(self) -> None:
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        client_kwargs: dict[str, Any] = {}
        if self.headers:
            client_kwargs["http_client"] = httpx.AsyncClient(headers=self.headers)

        async with streamable_http_client(self.url, **client_kwargs) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                self._tools = _format_tools(self.name, result.tools)
                self._ready.set()
                while True:
                    call = await self._queue.get()
                    if call is None:
                        break
                    try:
                        r = await session.call_tool(call.tool, call.arguments)
                        parts = [c.text for c in r.content if hasattr(c, "text")]
                        call.result.set_result("\n".join(parts))
                    except Exception as exc:
                        call.result.set_exception(exc)

    async def _run_sse(self) -> None:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(self.url, headers=self.headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                self._tools = _format_tools(self.name, result.tools)
                self._ready.set()
                while True:
                    call = await self._queue.get()
                    if call is None:
                        break
                    try:
                        r = await session.call_tool(call.tool, call.arguments)
                        parts = [c.text for c in r.content if hasattr(c, "text")]
                        call.result.set_result("\n".join(parts))
                    except Exception as exc:
                        call.result.set_exception(exc)

    async def call(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        bare = namespaced_name.removeprefix(f"mcp__{self.name}__")
        pending = _Call(tool=bare, arguments=arguments)
        await self._queue.put(pending)
        return await pending.result

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def stop(self) -> None:
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()


# Keep the old name as an alias so nothing outside breaks
MCPClient = StdioMCPClient


def _format_tools(server_name: str, tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"mcp__{server_name}__{t.name}",
                "description": t.description or "",
                "parameters": (
                    t.input_schema if isinstance(t.input_schema, dict) else {}
                ),
            },
        }
        for t in tools
    ]
