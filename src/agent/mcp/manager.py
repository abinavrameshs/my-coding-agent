"""MCP manager — starts/stops all configured MCP servers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.mcp.client import MCPClient

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.events.bus import EventBus

_GIT_SERVER_COMMAND = ["uvx", "mcp-server-git", "--repository", "."]


class MCPManager:
    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}

    async def start_all(self, cfg: "Config", bus: "EventBus", cwd: Path) -> None:
        from agent.events.bus import Event
        from agent.events.types import MCP_SERVER_START

        servers: dict[str, list[str]] = {
            name: srv.command for name, srv in cfg.mcp_servers.items()
        }

        # Auto-start git server when inside a git repo and not already configured
        if "git" not in servers and (cwd / ".git").exists():
            servers["git"] = _GIT_SERVER_COMMAND

        for name, command in servers.items():
            env = {}
            if name in cfg.mcp_servers:
                env = cfg.mcp_servers[name].env
            client = MCPClient(name=name, command=command, env=env)
            try:
                await client.start()
                self._clients[name] = client
                await bus.emit(Event(MCP_SERVER_START, {"server": name, "tools": len(client.tools)}))
            except Exception as exc:
                from agent.events.bus import Event
                from agent.events.types import ERROR
                await bus.emit(Event(ERROR, {"message": f"MCP server '{name}' failed to start: {exc}"}))

    async def stop_all(self) -> None:
        for client in self._clients.values():
            try:
                await client.stop()
            except Exception:
                pass
        self._clients.clear()

    def tool_list(self) -> list[dict[str, Any]]:
        """Return all tools from all running servers in OpenAI format."""
        tools = []
        for client in self._clients.values():
            tools.extend(client.tools)
        return tools

    async def call(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        """Route a namespaced tool call (mcp__<server>__<tool>) to the right client."""
        parts = namespaced_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            raise ValueError(f"Invalid MCP tool name: {namespaced_name!r}")
        server_name = parts[1]
        client = self._clients.get(server_name)
        if not client:
            raise ValueError(f"MCP server '{server_name}' is not running")
        return await client.call(namespaced_name, arguments)

    def is_mcp_tool(self, name: str) -> bool:
        return name.startswith("mcp__")
