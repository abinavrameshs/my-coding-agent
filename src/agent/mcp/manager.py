"""MCP manager — discovers, starts, and stops all configured MCP servers.

Config sources (merged in order, later wins):
  1. .agent/.mcp.json                   — project MCP config
  2. cfg.mcp_servers                    — from .agent/settings.json mcpServers key

No servers are started automatically — everything must be explicitly configured.

Supported formats in .agent/.mcp.json:
  {"servers": {"name": {"type": "http", "url": "..."}}}
  {"mcpServers": {"name": {"command": "npx", "args": [...]}}}
"""

from __future__ import annotations

import json
from pathlib import Path
import os
import re
from typing import TYPE_CHECKING, Any

from agent.mcp.client import HttpMCPClient, StdioMCPClient

if TYPE_CHECKING:
    from agent.config.config import Config, MCPServerConfig
    from agent.events.bus import EventBus

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR_NAME} in string values using os.environ."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def _load_mcp_json(cwd: Path) -> dict[str, "MCPServerConfig"]:
    """Read .agent/.mcp.json and return parsed server configs.

    String values support ${VAR_NAME} expansion from environment variables.
    Unexpanded variables (not set in env) are left as-is.
    """
    from agent.config.config import MCPServerConfig

    mcp_file = cwd / ".agent" / ".mcp.json"
    if not mcp_file.exists():
        return {}

    try:
        data = json.loads(mcp_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    raw_servers: dict[str, Any] = data.get("servers") or data.get("mcpServers") or {}
    result: dict[str, MCPServerConfig] = {}
    for name, raw in raw_servers.items():
        try:
            result[name] = MCPServerConfig.model_validate(_expand_env(raw))
        except Exception:
            pass
    return result


def _make_client(name: str, srv: "MCPServerConfig") -> StdioMCPClient | HttpMCPClient:
    transport = srv.type.lower()
    if transport in ("http", "sse", "streamable_http"):
        if not srv.url:
            raise ValueError(f"MCP server '{name}' has type='{transport}' but no url")
        return HttpMCPClient(name=name, url=srv.url, headers=srv.headers or {})
    # stdio (default)
    cmd = srv.resolved_command()
    args = srv.resolved_args()
    if not cmd:
        raise ValueError(f"MCP server '{name}' has no command")
    return StdioMCPClient(name=name, command=cmd, args=args, env=srv.env)


class MCPManager:
    def __init__(self) -> None:
        self._clients: dict[str, StdioMCPClient | HttpMCPClient] = {}

    async def start_all(self, cfg: "Config", bus: "EventBus", cwd: Path) -> None:
        from agent.config.config import MCPServerConfig
        from agent.events.bus import Event
        from agent.events.types import ERROR, MCP_READY, MCP_SERVER_START

        # Merge config sources: .agent/.mcp.json wins over settings mcpServers
        servers: dict[str, MCPServerConfig] = {**cfg.mcp_servers, **_load_mcp_json(cwd)}

        for name, srv in servers.items():
            try:
                client = _make_client(name, srv)
                await client.start()
                self._clients[name] = client
                await bus.emit(Event(MCP_SERVER_START, {
                    "server": name,
                    "tools": len(client.tools),
                    "transport": srv.type,
                }))
            except Exception as exc:
                msg = str(exc)
                hint = ""
                if any(k in msg.lower() for k in ("401", "403", "unauthorized", "forbidden")):
                    hint = " — check headers/auth in .agent/.mcp.json"
                elif "connect" in msg.lower() or "timeout" in msg.lower():
                    hint = " — check the url is reachable"
                await bus.emit(Event(ERROR, {
                    "message": f"MCP server '{name}' failed to start: {msg}{hint}"
                }))

        # Single summary event after all servers attempted
        await bus.emit(Event(MCP_READY, {
            "servers": {n: len(c.tools) for n, c in self._clients.items()},
            "total_tools": sum(len(c.tools) for c in self._clients.values()),
        }))

    async def start_server(
        self, name: str, srv_cfg: Any, cfg: "Config", bus: "EventBus", cwd: Path
    ) -> None:
        """Start a single MCP server by name and add it to the session."""
        from agent.config.config import MCPServerConfig
        from agent.events.bus import Event
        from agent.events.types import MCP_SERVER_START

        if not isinstance(srv_cfg, MCPServerConfig):
            srv_cfg = MCPServerConfig.model_validate(
                srv_cfg if isinstance(srv_cfg, dict) else {"command": srv_cfg}
            )
        client = _make_client(name, srv_cfg)
        await client.start()
        self._clients[name] = client
        await bus.emit(Event(MCP_SERVER_START, {
            "server": name,
            "tools": len(client.tools),
            "transport": srv_cfg.type,
        }))

    async def stop_all(self) -> None:
        for client in self._clients.values():
            try:
                await client.stop()
            except Exception:
                pass
        self._clients.clear()

    def tool_list(self) -> list[dict[str, Any]]:
        tools = []
        for client in self._clients.values():
            tools.extend(client.tools)
        return tools

    async def call(self, namespaced_name: str, arguments: dict[str, Any], cwd: Path | None = None) -> str:
        parts = namespaced_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            raise ValueError(f"Invalid MCP tool name: {namespaced_name!r}")
        server_name = parts[1]
        client = self._clients.get(server_name)
        if not client:
            raise ValueError(f"MCP server '{server_name}' is not running")
        # Auto-inject repo_path for git tools when not provided by the model
        if server_name == "git" and "repo_path" not in arguments and cwd:
            arguments = {**arguments, "repo_path": str(cwd)}
        return await client.call(namespaced_name, arguments)

    def is_mcp_tool(self, name: str) -> bool:
        return name.startswith("mcp__")
