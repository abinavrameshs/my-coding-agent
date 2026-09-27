# MCP integration

The agent uses the **Model Context Protocol (MCP)** as a primary extension mechanism.
Git operations, for example, arrive as MCP tools — there is no bespoke git handler.

- **Files:** `src/agent/mcp/client.py`, `src/agent/mcp/manager.py`
- **Prerequisite:** [`tool-system.md`](tool-system.md) §7, [`architecture.md`](architecture.md).

---

## 1. Overview

Two layers:

| Layer | Class | Role |
|---|---|---|
| Transport | `StdioMCPClient`, `HttpMCPClient` | One connection to one server; owns a background task; exposes `.tools`, `.call()`, `.start()`, `.stop()` |
| Lifecycle | `MCPManager` | Starts/stops all servers, exposes the merged tool list, routes calls by name |

`MCPClient = StdioMCPClient` is kept as an alias for backwards compatibility.

```
repl.py ──► MCPManager.start_all(cfg, bus, cwd)
                 │  reads .agent/.mcp.json + cfg.mcp_servers
                 │  for each server: create client, start(), collect tools
                 ▼
registry.py ──► mcp.tool_list()      (all tool schemas)
                 mcp.is_mcp_tool(name) (name.startswith("mcp__"))
                 mcp.call(name, args, cwd)  (route to the right server)
```

---

## 2. Tool naming / namespacing

Every MCP tool is registered under:

```
mcp__<server_name>__<tool_name>
```

e.g. `mcp__git__git_status`, `mcp__github__create_issue`. This guarantees MCP tool
names never collide with native tools. The client strips the prefix before forwarding
to the server (`removeprefix(f"mcp__{self.name}__")`).

- Git tools are recognised by the `mcp__git__` prefix and mapped to `ToolGroup.GIT`.
- All other MCP tools map to `ToolGroup.MCP`.
- `MCPManager.call` validates the `mcp__<server>__<tool>` shape and looks up the client
  by server name.

> **Gotcha — git `repo_path` auto-injection.** When routing a **git** tool and the
> model didn't supply `repo_path`, the manager injects `repo_path=<cwd>`. This is why
> git tools "just work" against the current project.

---

## 3. Configuration sources

Servers can be declared in two places; they are **merged, and `.mcp.json` wins** on
name collisions:

1. `cfg.mcp_servers` — from `.agent/settings.json` under an `mcpServers` (or
   `servers`) key.
2. `<cwd>/.agent/.mcp.json` — a dedicated MCP config file.

### `.agent/.mcp.json` formats

```jsonc
// stdio server (list form or split form)
{"servers": {"git": {"command": ["uvx", "mcp-server-git", "--repository", "."]}}}
{"mcpServers": {"echo": {"command": "npx", "args": ["-y", "@foo/server"], "env": {"TOKEN": "${MY_TOKEN}"}}}}

// HTTP / SSE server
{"servers": {"remote": {"type": "http", "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${API_TOKEN}"}}}}
```

> **Gotcha — `${VAR}` expansion.** String values in `.mcp.json` undergo
> `${ENV_VAR}` expansion from the process environment (`_expand_env`). Unset vars are
> left literally as `${VAR}`. Use this for secrets so they aren't committed.

> **Gotcha — nothing starts automatically.** The old PLAN.md said git is
> pre-configured by default. In the current code, **no server starts unless it is
> configured**. To get git tools you must declare an MCP server (see §6).

---

## 4. `MCPServerConfig` (the schema)

Defined in `config/config.py`. Accepts both stdio and HTTP.

| Field | Type | Meaning |
|---|---|---|
| `type` | `str` | `"stdio"` (default), `"http"`, `"sse"` |
| `command` | `str \| list[str]` | Executable, or `[exe, *args]` |
| `args` | `list[str]` | Extra args (when `command` is a string) |
| `env` | `dict[str,str]` | Extra environment variables |
| `url` | `str \| None` | For http/sse transports |
| `headers` | `dict[str,str]` | For http/sse transports |

Helpers: `resolved_command()` and `resolved_args()` normalise the two command forms.

---

## 5. Transports (`mcp/client.py`)

Both clients use the same pattern: `start()` launches a background `asyncio.Task`
running `_run()`, then awaits a `ready` event. Calls are queued and their results set
on per-call futures, so a single long-lived session serves all calls.

### `StdioMCPClient`
- Spawns the server as a subprocess via `mcp.StdioServerParameters` + `stdio_client`.
- `session.initialize()` → `session.list_tools()` → loop serving queued calls.
- `stop()` puts a sentinel (`None`) on the queue and awaits the task (5s timeout).

### `HttpMCPClient`
- Tries **Streamable HTTP** first (`streamable_http_client`), then falls back to
  **SSE** (`sse_client`). Optional `headers` are passed via an `httpx.AsyncClient`.
- If both fail, the error is stored and surfaced on `start()` (unwrapped from
  `ExceptionGroup` by `_unwrap`).

> **Gotcha — one background task per server, one session per task.** Do not call a
> client's `.call()` before `.start()` resolved the ready event, or you'll queue onto a
> session that doesn't exist yet.

---

## 6. Adding an MCP server

### Project-local (recommended)
Create `.agent/.mcp.json`:

```json
{
  "servers": {
    "git": {
      "command": ["uvx", "mcp-server-git", "--repository", "."]
    }
  }
}
```

Restart the agent. You should see `⬡ MCP  git(12)  [12 tools]` at startup.

### Ad-hoc for one session (REPL)
```
/mcp add git uvx mcp-server-git --repository .
```
This calls `MCPManager.start_server(...)` and invalidates the registry schema cache so
the new tools appear immediately.

### Via settings
Put the same shape under `mcpServers` in `.agent/settings.json` (see
`configuration.md`).

### Verify
- `/mcp` — lists connected servers + their tools.
- `/tools on git` — ensure the `git` group is active.
- Then ask the agent to "check git status".

---

## 7. Startup / failure behaviour

`MCPManager.start_all`:
- Starts **all servers in parallel** (`asyncio.gather`) — startup is `max(latency)`,
  not `sum(latency)`.
- A server that fails to start emits an `error` event with a **hint**:
  - 401/403/unauthorized/forbidden → "check headers/auth in .agent/.mcp.json"
  - connect/timeout → "check the url is reachable"
- Other servers still start. The REPL prints `MCP startup warning: ...` on failure.
- After all attempts, a single `mcp.ready` event summarises servers + total tools.

`stop_all()` stops every client (swallowing errors) and clears the map; called at the
end of `run_repl` and in single-shot mode.

---

## 8. Routing a call (flow)

1. Model returns a tool call named e.g. `mcp__git__git_status`.
2. `loop.run_turn` → `registry.dispatch` → `registry._run`.
3. `_run` sees `mcp.is_mcp_tool(name)` is true, records the server in `_servers_used`,
   and calls `mcp.call(name, args, cwd)`.
4. `MCPManager.call` splits the name, finds the client, (for git) injects `repo_path`,
   and calls `client.call(name, args)`.
5. The client strips the prefix, queues the bare tool + args, and awaits the result.
6. The result string is returned up through `dispatch`, which wraps it in
   `<tool_result>…</tool_result>` for the model.

---

## 9. Key files

- `src/agent/mcp/client.py` — `StdioMCPClient`, `HttpMCPClient`
- `src/agent/mcp/manager.py` — `MCPManager`, config loading, routing
- `src/agent/config/config.py` — `MCPServerConfig`

## See also

- [`tool-system.md`](tool-system.md) — groups, dispatch, `schemas_for` filtering
- [`configuration.md`](configuration.md) — `mcpServers` in settings
- [`events-and-listeners.md`](events-and-listeners.md) — `mcp.server_start`, `mcp.ready`