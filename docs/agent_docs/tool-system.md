# The tool system

How tools are defined, grouped, filtered, presented to the model, and dispatched.
All of this lives in **`src/agent/tools/registry.py`** plus the individual tool
modules under `src/agent/tools/`.

- **Prerequisite:** [`architecture.md`](architecture.md) §3–§4.
- **To add a tool:** jump to [`adding-a-tool.md`](adding-a-tool.md).

---

## 1. What a "tool" is here

A tool is a Python callable plus an **OpenAI-format schema** describing it. The
schema is what the model sees; the callable is what runs.

```python
# A tool schema (OpenAI function-calling format — note "parameters", not "input_schema")
{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file's contents with line numbers.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}
```

Each native tool module exports:
- `SCHEMA: dict` (for single-tool modules like `bash.py`) **or** `SCHEMAS: list[dict]`
- `HANDLERS: dict[str, callable]` mapping tool name → handler (except `bash.py`,
  whose handler is `run_bash` imported directly)

The registry imports these and wires them together.

---

## 2. Tool groups

Tools are bucketed into four groups (`ToolGroup` enum in `registry.py`). Only the
schemas of **active** groups are sent to the model — this keeps the tool list small
and the model focused.

| Group | Members | Default | Enabled by |
|---|---|---|---|
| `CORE` | `read_file`, `write_file`, `edit_file`, `grep_files`, `find_files`, `bash`, `todo_write`, `todo_read`, `remember`, `forget` | **always on** | — |
| `WEB` | `web_search`, `web_fetch` | off | `--web` / `web_search=true` / JEV routing / `/tools on web` |
| `GIT` | all `mcp__git__*` tools | off | JEV routing / `/tools on git` (only when `.git` exists) |
| `MCP` | every other `mcp__*` tool | off | JEV routing / `/tools on mcp` |

> **Note — plugins are not a group.** Tools loaded from `agent_tools/*.py` are added
> to `_base_tools()` unconditionally and are never filtered out. There is no
> `ToolGroup.PLUGIN`.

### Enabling / disabling at runtime
- `registry.enable(group)` / `registry.disable(group)` — both invalidate the schema
  cache.
- Interactive: `/tools on git`, `/tools off web`, `/tools` (lists state).
- Automatic: `routing.auto_enable_groups` asks JEV per user message (see below).

---

## 3. What the model sees: `schemas()` vs `schemas_for(hint)`

Two methods build the tool list handed to the LLM:

### `schemas() -> list[dict]`
The full active list, **cached** in `self._schema_cache`. Built from:
1. `_base_tools()` — core + (web if active) + plugins (always)
2. `_mcp_by_server()` — active MCP tool schemas, grouped by server

Cache is invalidated by `enable` / `disable` / `invalidate_schema_cache()`.

### `schemas_for(hint) -> list[dict]`
`loop.run_turn` calls this with the **last user message** as `hint`. It excludes MCP
servers that are irrelevant to the message, to shrink the tool list further.

Algorithm:
1. Extract keywords from `hint` (`_keywords`, stop-words filtered).
2. **Bail to full `schemas()`** if:
   - `len(hint_words) < 6` (not enough signal), **or**
   - there are no MCP servers, **or**
   - **every** server scores 0 (no signal to act on).
3. Otherwise, score each MCP server by keyword overlap between its
   server-name + tool names + descriptions and the hint.
4. Include a server's tools if it scored `> 0` **or** it's in `self._servers_used`
   (servers already called this session are always kept).
5. Core / web / plugin tools are **always** included.

> **Gotcha — two different MCP gates.** `schemas_for` does *relevance* filtering
> (keyword-based). Group *activation* (`GIT`/`MCP` in `_active`) is separate. A git
> server can be relevant but excluded because `ToolGroup.GIT` is off, or active but
> filtered out because the hint is unrelated.

> **Gotcha — `_servers_used` grows for the session.** Once a server's tool is
> dispatched, `_run` adds its name to `_servers_used`, so it's never filtered out
> again. This prevents the model from "losing" a tool mid-task.

---

## 4. Dispatch: how a call is routed

`loop.run_turn` calls `registry.dispatch(tool_name, arguments)`. Dispatch is the
single funnel for **all** tool execution.

```python
async def dispatch(self, tool_name, arguments) -> str:
    try:
        raw = await self._run(tool_name, arguments)
    except ToolError as e:
        raw = f"Error: {e}"
    except Exception as e:
        raw = f"Unexpected error in {tool_name}: {e}"
    return wrap_tool_result(tool_name, raw)   # injection wrapper
```

`_run` resolves the handler in this order:

| Check | Route |
|---|---|
| `mcp.is_mcp_tool(name)` (starts with `mcp__`) | `mcp.call(name, args, cwd)`; records `_servers_used` |
| name in `FILE_HANDLERS` | file handler `(args, cwd)` |
| name == `"bash"` | `run_bash(command, cwd, timeout, max_output_chars)` |
| name in `TODO_HANDLERS` | todo handler `(args, cwd)` |
| name in `MEMORY_HANDLERS` | memory handler `(args, cwd)` |
| name in `WEB_HANDLERS` | web handler (awaited if async) |
| name in `self._plugins` | `plugin.call(args)` |
| otherwise | `raise ToolError("Unknown tool: …")` |

> **Rule — every tool result is wrapped.** `wrap_tool_result` produces
> `<tool_result name="X">…</tool_result>`. This is the prompt-injection defence: the
> model is instructed to treat wrapped content as external data, never instructions.
> Never bypass it.

> **Rule — handler signature conventions.** File/bash/todo/memory handlers are
> **sync** and take `(arguments, cwd)` (bash takes the raw command + flags). Web
> handlers may be **async** (`web_fetch` is) and take only `(arguments)`. Plugins are
> **async** `run(arguments)`. Match the pattern when adding tools.

---

## 5. Native tools catalogue

| Tool | Group | Handler | Notes |
|---|---|---|---|
| `read_file` | core | `files.read_file` | Line-numbered output; optional `start_line`/`end_line` |
| `write_file` | core | `files.write_file` | Creates parent dirs; overwrites |
| `edit_file` | core | `files.edit_file` | `old_string` must be **unique** (errors otherwise) |
| `grep_files` | core | `files.grep_files` | Uses system `grep`; Python fallback; `include` glob + `max_results` |
| `find_files` | core | `files.find_files` | `rglob`; **excludes hidden dirs** |
| `bash` | core | `bash.run_bash` | Timeout (default 30s), output cap, dangerous-pattern warning |
| `todo_write` / `todo_read` | core | `todo.py` | In-memory list; replaced wholesale each `todo_write` |
| `remember` / `forget` | core | `memory_tools.py` | Persist facts to `.agent/memory.json` |
| `web_search` | web | `web.web_search` | DuckDuckGo via `ddgs` |
| `web_fetch` | web | `web.web_fetch` | `httpx`, HTML→text, 8000-char cap |
| `spawn_subagent` | (injected) | `subagent.py` | Added by `_SubagentRegistry`, not the base registry |
| `spawn_parallel` | (injected) | `subagent.py` | Runs a batch of plan tasks concurrently; see §10 |
| `mcp__<server>__<tool>` | git/mcp | MCP client | Namespaced; see `mcp-integration.md` |

### Path safety
`files._safe_path` resolves every path against `cwd` and raises `ToolError` if it
escapes the working directory. `write_file`/`edit_file`/`read_file` all use it.

### Bash safety
`bash._DANGEROUS_PATTERNS` (`rm -rf`, `sudo `, `curl | sh`, `dd if=`, `mkfs`, fork
bomb, …) only adds a **warning prefix** to the output — it does not block. Actual
gating is the approval listener's job (see `events-and-listeners.md`).

---

## 6. Plugin tools

Drop a file in `./agent_tools/<name>.py` exporting:
- `SCHEMA: dict` — OpenAI function schema (auto-wrapped if bare)
- `async def run(input: dict) -> str` — the implementation (sync is wrapped too)

`plugins.load_plugins(cwd)` scans the directory at registry construction. A file
that fails to import, or is missing `SCHEMA`/`run`, is logged and **skipped** — it
never crashes startup. Runtime exceptions are returned to the model as a traceback
string. See `agent_tools/echo.py` (working) and `agent_tools/bad.py` (skipped).

> **Gotcha — plugin files starting with `_` are ignored.** Use that to keep helper
> modules out of the plugin namespace.

> **Gotcha — plugins run in-process with no sandbox.** They have full Python access.
> Documented as a security consideration in `../PLAN.md`.

---

## 7. JEV tool-group routing

`routing.auto_enable_groups(user_message, registry, cwd, cfg, console)`:
- Only proposes groups that are **not already active** and are **plausible**:
  - `git` — only if `(cwd / ".git").exists()`
  - `web` — only if not already enabled via `cfg.web_search`
  - `mcp` — only if there are non-git MCP tools
- Builds one `noul` question per candidate and calls the JEV decisions endpoint.
- Enables the groups JEV returns `True` for (probability ≥ `cfg.jev_threshold`) and
  prints `⚡ auto-enabled: …`.
- On any failure it does nothing — core tools remain available.

---

## 8. Inspecting tools at runtime

- `/tools` — lists active groups with counts and all active tool names/descriptions.
- `/tools on <group>` / `/tools off <group>` — toggle a group.
- `/mcp` — lists MCP servers and their tools.
- `registry.tool_count_by_group()` — the counts behind `/tools`.

---

## 9. Subagent tools (injected, not in the base registry)

`spawn_subagent` and `spawn_parallel` are **not** part of `ToolRegistry`. They are
injected by `_SubagentRegistry` (`subagent.py`), which wraps the base registry and:

- adds both schemas in `schemas()` **and** `schemas_for(hint)` — the loop calls
  `schemas_for`, so both must be overridden or the tools never reach the model;
- gates them on depth: present while `depth < MAX_DEPTH` (3), absent at the limit;
- intercepts dispatch for the two names and delegates everything else via
  `__getattr__`.

| Tool | Purpose |
|---|---|
| `spawn_subagent` | One isolated sub-loop; returns its final text. Parallel only if the model emits several in one turn. |
| `spawn_parallel` | A **batch** of tasks with an optional `dependencies` map; the scheduler decides what runs concurrently. |

### `spawn_parallel` scheduling (`parallel.py`)

`parallel.py` is pure (no I/O, no LLM) and holds the whole policy:

1. `normalize_deps` — validates the `dependencies` map, dropping unknown ids,
   self-deps, and duplicates. Malformed input degrades to "all independent".
2. `plan_batches` — topological levels, each capped at `cfg.max_parallel_subagents`.
   A dependency cycle degrades to a fully serial batch list.
3. `group_by_files` — splits any batch so two tasks that declare the same file in
   `files` never run together (parallel editors of one file would race).

`run_parallel` executes each batch with `asyncio.gather`, emitting
`parallel.batch_start` / `parallel.batch_end` per batch, and aggregates results
as `### Task <id>` sections in original order. Fail-soft: a per-task failure is
captured as text, never raised.

> **Config:** `cfg.parallel_planning` (default `True`) and
> `cfg.max_parallel_subagents` (default `4`) control this. See `configuration.md`.

---

## 10. Key files

- `src/agent/tools/registry.py` — the whole system
- `src/agent/tools/{files,bash,web,todo,memory_tools}.py` — native tools
- `src/agent/tools/plugins.py` — plugin loader
- `src/agent/routing.py` — JEV group routing + `classify_plan_items`
- `src/agent/subagent.py` — `spawn_subagent` / `spawn_parallel` injection
- `src/agent/parallel.py` — dependency batching + file-conflict policy

## See also

- [`adding-a-tool.md`](adding-a-tool.md) — copy-paste recipes
- [`events-and-listeners.md`](events-and-listeners.md) — `tool.before` / `tool.after`
- [`mcp-integration.md`](mcp-integration.md) — MCP tool namespacing & dispatch