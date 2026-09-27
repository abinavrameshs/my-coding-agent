# Python CLI Coding Agent — Build Plan

> ⚠️ **HISTORICAL DESIGN DOCUMENT — PARTLY STALE.**
> This is the *original* plan written before implementation. The shipped code has
> diverged from it. **Do not treat it as a description of the current system.**
> For the current architecture, read [`../AGENT.md`](../AGENT.md) and
> [`agent_docs/`](agent_docs/README.md).
>
> Known divergences (plan → reality):
> - **LLM SDK:** plan says Anthropic (`client.messages.stream()`, `input_schema`);
>   the code uses the **OpenAI SDK** (`client.chat.completions.create`,
>   `"parameters"`). See `src/agent/loop.py`.
> - **Settings hierarchy:** plan says 4 levels incl. `~/.agent/settings.json`; the code
>   reads **3** (defaults → project files → CLI). See `src/agent/config/config.py`.
> - **Events:** the plan's event catalog (`message.user`, `tool.approved`,
>   `tool.denied`, `tool.error`, `memory.write`, `session.save`, `session.load`) is
>   **not** what the code emits. See `src/agent/events/types.py`.
> - **JEV router:** not in the plan at all — `src/agent/routing.py` adds a decisions
>   model for tool routing, bash approval, and memory. 
> - **Tool groups & skills:** tool groups (`core/web/git/mcp`), `.agent/skills/`, and
>   `.agent/commands/` are not described in the plan.
> - **Git via MCP:** the plan says git is "pre-configured by default"; the code starts
>   **no** MCP server unless it is explicitly configured.
> - **Config defaults:** the plan's example model (`deepseek/deepseek-chat-v3-0324`)
>   differs from the code default (`deepseek/deepseek-v4.1-flash`).

## Project Overview

Build a Python CLI coding agent similar to Claude Code from scratch. The agent accepts natural-language instructions and autonomously reads, writes, edits, and executes code using Claude as its reasoning engine. It runs entirely from the terminal and is packaged as a proper Python CLI tool using `uv`.

---

## Requirements

### Functional Requirements

| # | Requirement | Priority |
|---|-------------|----------|
| F1 | Interactive REPL loop accepting multi-turn natural language input | Must |
| F2 | Read files from disk and pass contents to Claude | Must |
| F3 | Write new files to disk | Must |
| F4 | Edit existing files with exact string replacement | Must |
| F5 | Execute bash commands and return stdout/stderr | Must |
| F6 | Grep/search for patterns across files | Must |
| F7 | Glob/find files matching a pattern | Must |
| F8 | Stream Claude's responses to the terminal in real time | Must |
| F9 | Multi-turn conversation with full context window management | Must |
| F10 | Tool call approval mode (ask before destructive operations) | Must |
| F11 | Single-shot (`-p "prompt"`) non-interactive mode | Must |
| F12 | Load AGENT.md / CLAUDE.md system-prompt from cwd | Must |
| F13 | Display token usage and cost estimate per turn | Must |
| F14 | Cancel a running tool call with Ctrl-C | Must |
| F15 | Conversation history compaction when context fills up | Must |
| F16 | MCP client — connect to local MCP servers over stdio or HTTP | Must |
| F17 | Persist conversation history to disk; resume a previous session | Must |
| F18 | Plugin tools — user-defined tools from `./agent_tools/` | Must |
| F19 | Multi-file diff approval with `[y/N/all/quit]` per-file flow | Must |
| F20 | Web search via `web_search_20260209` (enabled with `--web` flag) | Must |
| F21 | Subagents — spawn parallel sub-agents for divisible tasks | Must |
| F22 | Event bus with typed events and pluggable listener registration | Must |
| F23 | **Plan mode** — Claude proposes a numbered plan before executing; user approves/edits | Must |
| F24 | **Auto memory** — Claude writes learnings to `~/.agent/memory/` after each session | Must |
| F25 | **Git via MCP** — all git ops through `mcp-server-git`; no bespoke git tool handlers | Must |
| F26 | **Hook input modification** — `tool.before` listeners can rewrite tool inputs before execution | Must |
| F27 | **Settings hierarchy** — 4-level config: managed → project-shared → project-local → user | Must |
| F28 | **Scoped rules** — `.agent/rules/*.md` files applied only to matching file types | Must |
| F29 | **JSONL transcript** — every event written as a JSON line for audit and hook consumption | Must |
| F30 | **Extended slash commands**: `/compact`, `/cost`, `/plan`, `/init`, `/memory`, `/mcp` | Must |

### Non-Functional Requirements

| # | Requirement |
|---|-------------|
| N1 | All dependencies managed via `uv`; no `pip install` required |
| N2 | No secrets hardcoded; auth via `OPENROUTER_API_KEY` env var (or any OpenAI-compatible provider key) |
| N3 | Graceful handling of API errors (rate limits, network failures) with retry |
| N4 | Works on macOS and Linux |
| N5 | All file writes must show a diff preview in approval mode |
| N6 | Command execution sandbox: warn on commands that modify system state |
| N7 | Streaming output must not block the event loop |
| N8 | All side-effects (display, approval, logging, persistence) implemented as event listeners, not hardcoded in the agent loop |
| N9 | MCP is the primary extension mechanism; git, filesystem extras, and third-party integrations all arrive via MCP servers |

---

## Technology Choices

| Concern | Choice | Why |
|---------|--------|-----|
| Package manager | `uv` | Fast, lockfile-based, PEP 517 compliant |
| CLI framework | `typer` (wraps Click) | Automatic `--help`, type-annotated args, async-friendly |
| LLM SDK | `openai` ≥ 1.x | OpenAI-compatible; works with OpenRouter, DeepSeek, Claude, Groq, and any other provider |
| LLM provider | OpenRouter (`https://openrouter.ai/api/v1`) | Unified API for 200+ models; pay-as-you-go; no lock-in |
| Default model | `deepseek/deepseek-chat-v3-0324` | Excellent coding capability; ~20× cheaper than Opus-class models |
| Syntax highlighting | `rich` | Pretty diffs, markdown rendering, panels, progress |
| Config / env | `python-dotenv` | `.env` file loading alongside env vars |
| MCP client | `mcp` (Python SDK) | Official MCP client library; handles stdio/HTTP transports |
| Git operations | `mcp-server-git` | Official git MCP server; `uvx mcp-server-git --repository .` |
| Web search | `duckduckgo-search` | Free, no API key; client-side tool (not a server-side Anthropic tool) |
| Web fetch | `httpx` | Async HTTP client for fetching URL content |

---

## Architecture

```
my-coding-agent/
├── pyproject.toml                  # uv project, entry point `agent`
├── uv.lock
├── AGENT.md                        # default system prompt
├── docs/
│   └── PLAN.md
└── src/
    └── agent/
        ├── __init__.py
        ├── main.py                 # CLI entry point (typer app)
        ├── loop.py                 # agent loop: stream → emit events → tool dispatch → loop
        ├── plan.py                 # plan mode: propose → user approval → execute
        ├── events/
        │   ├── __init__.py
        │   ├── bus.py              # EventBus: register listeners, emit events
        │   ├── types.py            # typed Event constants for every lifecycle point
        │   └── listeners/
        │       ├── __init__.py
        │       ├── display.py          # renders events to the terminal (Rich)
        │       ├── approval.py         # prompts user before destructive tool calls
        │       ├── persistence.py      # saves / loads conversation history
        │       ├── cost_tracker.py     # accumulates token usage and cost
        │       ├── transcript.py       # writes every event as JSONL to disk
        │       ├── auto_memory.py      # writes session learnings to ~/.agent/memory/
        │       └── webhook.py          # POSTs selected events to a configured URL
        ├── tools/
        │   ├── __init__.py
        │   ├── registry.py         # native tool schemas + plugin loader
        │   ├── bash.py             # run_bash
        │   ├── files.py            # read_file, write_file, edit_file
        │   ├── search.py           # grep_files, find_files
        │   └── web.py              # web_search (server-side tool)
        ├── mcp/
        │   ├── __init__.py
        │   ├── client.py           # MCP client: connect, list tools, call tools
        │   ├── manager.py          # lifecycle: start servers, merge tool lists, route calls
        │   └── servers.py          # built-in server configs (git, etc.)
        ├── memory/
        │   ├── __init__.py
        │   ├── loader.py           # loads AGENT.md + scoped rules at session start
        │   ├── auto.py             # post-session auto memory extraction and save
        │   └── rules.py            # .agent/rules/*.md scoped-rule matcher
        ├── config/
        │   ├── __init__.py
        │   └── settings.py         # 4-level settings hierarchy with precedence
        ├── subagent.py             # spawn and coordinate parallel sub-agents
        ├── context.py              # conversation history + compaction
        ├── session.py              # session ID, disk persistence, resume
        ├── display.py              # rich-based terminal rendering primitives
        └── transcript.py           # JSONL transcript writer
```

### Dependency Direction

```
main.py
  └── loop.py  (only layer that calls the Anthropic SDK)
        ├── plan.py               (plan mode gating)
        ├── events/bus.py         (emit lifecycle events)
        ├── context.py            (conversation history + compaction)
        ├── subagent.py           (spawn sub-agents)
        ├── tools/registry.py     (native tools)
        └── mcp/manager.py        (MCP tool routing)

events/listeners/*  (register on the bus; never imported by loop.py)
memory/*            (loaded at startup; not called mid-loop)
config/settings.py  (read-only after startup)
```

Rules:
- `tools/` and `mcp/` never import from `loop.py`.
- `loop.py` never imports from `events/listeners/` — it only emits on the bus.
- Listeners register at startup in `main.py`. The loop knows about the bus, not the listeners.
- All cross-cutting concerns (display, approval, logging, cost, persistence, memory) live in listeners.

---

## Git via MCP Server

Git operations use `mcp-server-git` instead of a bespoke tool handler. This makes MCP load-bearing from day one and means any future MCP server drops in with zero new code.

### Why MCP for Git

| Approach | Pros | Cons |
|---|---|---|
| Native git tools (subprocess) | No external dep, fast | Must write and maintain handlers; just another special case |
| `mcp-server-git` | Maintained externally, correct, consistent with MCP architecture | Subprocess dependency |

`mcp-server-git` is a `uvx`-installable Python package. The agent starts it as a subprocess at session init and communicates over stdio — no daemon, no port, no setup beyond `uvx`.

### MCP Client Architecture

```
loop.py
  └── mcp/manager.py
        ├── Starts servers: subprocess(["uvx", "mcp-server-git", "--repository", "."])
        ├── Discovers tools via MCP initialize / tools/list
        ├── Merges MCP tools into the tool list passed to Claude
        └── Routes tool_use blocks with "mcp__<server>__<tool>" names back to the server
```

### Git Tools Exposed by `mcp-server-git`

| MCP tool | What it does |
|---|---|
| `git_status` | Working tree status |
| `git_diff_unstaged` | Unstaged changes |
| `git_diff_staged` | Staged changes |
| `git_diff` | Diff between any two refs |
| `git_commit` | Stage and commit with a message |
| `git_add` | Stage files |
| `git_reset` | Unstage files |
| `git_log` | Recent commit history |
| `git_create_branch` | Create and switch to a new branch |
| `git_checkout` | Switch branch |
| `git_list_branches` | List local branches |
| `git_show` | Show a commit's details |

### MCP Client Design

```python
# mcp/client.py
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPClient:
    async def connect(self, name: str, command: list[str]) -> None:
        params = StdioServerParameters(command=command[0], args=command[1:])
        self._session = await stdio_client(params).__aenter__()
        await self._session.initialize()

    async def list_tools(self) -> list[dict]:
        result = await self._session.list_tools()
        return [tool.model_dump() for tool in result.tools]

    async def call_tool(self, name: str, input: dict) -> str:
        result = await self._session.call_tool(name, input)
        return result.content[0].text
```

`MCPManager` holds one `MCPClient` per configured server. Tool names are namespaced as `mcp__<server_name>__<tool_name>` in Claude's tool list so they never collide with native tools.

### Configuring MCP Servers

In `.agent/settings.json` (project-shared) or `~/.agent/settings.json` (user):

```json
{
  "mcpServers": {
    "git": {
      "command": ["uvx", "mcp-server-git", "--repository", "."]
    },
    "memory": {
      "command": ["npx", "-y", "@modelcontextprotocol/server-memory"]
    }
  }
}
```

The `git` server is pre-configured by default (enabled automatically when a `.git` directory exists in cwd). Users add more servers via `~/.agent/settings.json` or the `/mcp add` command.

---

## Events / Listeners System

The agent loop emits typed events at every lifecycle point. Listeners subscribe and react. The loop has no knowledge of what listeners exist.

### Event Catalog

| Event type | When emitted | Key payload fields | Special behaviour |
|---|---|---|---|
| `session.start` | Session begins | `session_id`, `model`, `cwd`, `flags` | — |
| `session.end` | Session ends or Ctrl-C | `session_id`, `total_usage`, `duration_s` | — |
| `turn.start` | Before each API call | `messages`, `turn_index` | — |
| `turn.end` | After API call returns | `response`, `usage`, `stop_reason` | — |
| `stream.delta` | Each streamed text chunk | `text`, `turn_index` | — |
| `message.user` | User submits input | `text` | — |
| `message.assistant` | Full assistant text ready | `text` | — |
| `plan.proposed` | Plan mode: Claude outputs a plan | `plan_text` | Listener presents plan; waits for approval |
| `plan.approved` | User approved the plan | `plan_text` | Loop begins executing |
| `plan.rejected` | User rejected / edited the plan | `edited_plan` | Loop re-proposes with edits |
| `tool.before` | Before tool execution | `tool_name`, `tool_id`, `input`, `cancelled`, `updated_input` | Listener can set `cancelled=True` OR set `updated_input` to rewrite args |
| `tool.after` | After tool execution | `tool_name`, `tool_id`, `input`, `result`, `duration_ms` | — |
| `tool.approval_requested` | Approval gate triggered | `tool_name`, `input`, `diff` | Listener sets `approved=True/False` |
| `tool.approved` | Tool approved | `tool_name`, `input` | — |
| `tool.denied` | Tool denied by user | `tool_name`, `input` | — |
| `tool.error` | Tool raised an exception | `tool_name`, `input`, `error` | — |
| `mcp.server_start` | MCP server process started | `server_name`, `command` | — |
| `mcp.tool_call` | MCP tool invoked | `server_name`, `tool_name`, `input` | — |
| `context.compact` | Compaction triggered | `tokens_before`, `tokens_after`, `summary` | — |
| `memory.write` | Auto memory item saved | `key`, `value`, `path` | — |
| `subagent.start` | Sub-agent spawned | `subagent_id`, `prompt` | — |
| `subagent.end` | Sub-agent finished | `subagent_id`, `result` | — |
| `session.save` | History written to disk | `path`, `session_id` | — |
| `session.load` | History loaded from disk | `path`, `session_id`, `turn_count` | — |
| `error` | Any unhandled error | `error`, `context` | — |

### EventBus Design

```python
# events/bus.py
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
import asyncio

EventHandler = Callable[["Event"], Awaitable[None] | None]

@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

class EventBus:
    def __init__(self):
        self._listeners: dict[str, list[EventHandler]] = {}

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Register for a specific event type, or '*' for all events."""
        self._listeners.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        if event_type in self._listeners:
            self._listeners[event_type].remove(handler)

    async def emit(self, event: Event) -> None:
        for handler in self._listeners.get(event.type, []):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        for handler in self._listeners.get("*", []):
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
```

### Hook Input Modification (`updated_input`)

Listeners on `tool.before` can now do two things: cancel the call, or rewrite its arguments before execution. The loop checks `updated_input` after emitting:

```python
# loop.py
before = Event("tool.before", {
    "tool_name": block.name,
    "tool_id": block.id,
    "input": block.input,
    "cancelled": False,
    "updated_input": None,        # ← new
})
await bus.emit(before)

if before.data["cancelled"]:
    tool_results.append(build_result(block.id, "Cancelled."))
    continue

effective_input = before.data["updated_input"] or block.input   # ← use rewrite if set
result = await dispatch_tool(block.name, effective_input)
```

**Use cases for `updated_input`:**
- Auto-run `black` on `write_file` content before saving
- Strip trailing whitespace from all file writes
- Expand `~` and relative paths to absolute paths in any file tool
- Inject extra safety flags into bash commands

### Built-in Listeners

| Listener | Subscribes to | What it does |
|---|---|---|
| `DisplayListener` | `stream.delta`, `message.assistant`, `tool.before`, `tool.after`, `plan.proposed`, `turn.end`, `error` | Renders everything to the terminal using Rich |
| `ApprovalListener` | `tool.approval_requested` | Prompts `[y/N/all/quit]`, sets `approved` on the event |
| `CostTrackerListener` | `turn.end`, `session.end` | Accumulates token counts; prints summary on session end |
| `TranscriptListener` | `*` | Writes every event as a JSON line to `~/.agent/transcripts/<session_id>.jsonl` |
| `PersistenceListener` | `turn.end`, `session.start`, `session.end` | Saves and loads conversation history (messages only) |
| `AutoMemoryListener` | `session.end` | Prompts Claude to extract learnings; saves to `~/.agent/memory/<project_hash>.json` |
| `WebhookListener` | configurable | POSTs selected events to a configured URL |

---

## Plan Mode

Before executing a complex task, Claude proposes a numbered plan and waits for user approval. This prevents a wrong assumption from cascading through many tool calls.

### Flow

1. User sends a task (e.g. "refactor the auth module to use JWT").
2. `plan.py` sends the prompt to Claude with an extra system instruction: *"Before executing, output a numbered plan of exactly what you will do. Wait for approval."*
3. Claude responds with a plan (text only, no tool calls yet).
4. `plan.proposed` event fires. `DisplayListener` renders the plan and prompts: `[Enter to approve / e to edit / q to cancel]`.
5. User approves → `plan.approved` fires → `loop.py` continues with execution.
6. User edits → plan text updated → `plan.rejected` fires → re-proposed with user's changes.
7. User cancels → session returns to the REPL prompt.

### Activation

Plan mode is on by default for multi-step tasks. It can be:
- Triggered explicitly: `/plan your task here`
- Disabled per session: `--no-plan`
- Disabled globally: `"planMode": false` in settings

```python
# plan.py
PLAN_SYSTEM_ADDENDUM = """
Before taking any action, output a concise numbered plan of what you will do.
Label it clearly with "## Plan". Do not call any tools yet.
Wait for the user to approve the plan before proceeding.
"""
```

---

## Auto Memory

After each session, Claude reflects on what it learned and writes short factual notes to `~/.agent/memory/<project_hash>.json`. These notes are injected into the system prompt at the start of the next session.

### What Gets Remembered

- Preferences the user stated (e.g. "use `httpx`, not `requests`")
- Corrections the user made (e.g. "I use 2-space indents here")
- Project conventions discovered (e.g. "tests live in `tests/`, not `test/`")
- Things that failed (e.g. "running `make test` requires the venv to be active first")

### Implementation

`AutoMemoryListener` fires on `session.end`. It sends a summarization request to Claude:

```
System: You are a memory extractor. Given a conversation transcript, extract
        short factual notes the agent should remember for future sessions in
        this project. Output JSON: {"memories": [{"key": str, "value": str}]}.
        Only include things that would not be obvious from reading the code.
        Omit anything ephemeral or task-specific.

User: <last N turns of transcript>
```

The resulting memories are merged (not replaced) into the project memory file. On the next session start, `memory/loader.py` injects them into the system prompt as a `## Remembered context` section.

### Memory File Format

```json
{
  "project": "my-coding-agent",
  "updated_at": "2026-09-26T10:00:00Z",
  "memories": [
    {"key": "test_command", "value": "Run tests with: uv run pytest"},
    {"key": "indent_style", "value": "4 spaces, enforced by ruff"},
    {"key": "no_requests", "value": "Use httpx, not requests"}
  ]
}
```

Users can view and edit memory with `/memory` in the REPL.

---

## Settings Hierarchy

Four levels of settings, highest wins:

| Level | File | Scope | Committed? |
|---|---|---|---|
| 1 (highest) | CLI flags | This invocation only | n/a |
| 2 | `.agent/settings.local.json` | This project, this user | No (gitignored) |
| 3 | `.agent/settings.json` | This project, all users | Yes |
| 4 (lowest) | `~/.agent/settings.json` | All projects, this user | No |

```python
# config/settings.py
def load_settings() -> dict:
    user   = load_json("~/.agent/settings.json")
    shared = load_json(".agent/settings.json")
    local  = load_json(".agent/settings.local.json")
    # deep merge: local > shared > user
    return deep_merge(user, shared, local)
```

Key settings:

```json
{
  "model": "deepseek/deepseek-chat-v3-0324",
  "baseUrl": "https://openrouter.ai/api/v1",
  "approvalMode": "auto",
  "planMode": true,
  "webSearch": false,
  "mcpServers": { "git": { "command": ["uvx", "mcp-server-git", "--repository", "."] } },
  "disallowedTools": [],
  "allowedTools": [],
  "webhookUrl": null,
  "autoMemory": true,
  "transcriptDir": "~/.agent/transcripts"
}
```

---

## Scoped Rules

`.agent/rules/` holds Markdown files that are only injected into the system prompt when Claude is working on matching file types.

```
.agent/
└── rules/
    ├── python.md       # injected when editing .py files
    ├── typescript.md   # injected when editing .ts / .tsx files
    └── migrations.md   # injected when editing files under db/migrations/
```

Each file has optional YAML frontmatter:

```yaml
---
globs: ["*.py", "src/**/*.py"]
---

- Always use type hints on public functions.
- Prefer dataclasses over plain dicts for structured data.
- Run `ruff check` after any edit.
```

`memory/rules.py` checks which rules match the files currently in context and appends them to the system prompt section.

---

## Implementation Plan

### Phase 0 — Project Scaffolding

**Goal:** `uv run agent` prints a hello message.

1. `uv init my-coding-agent --package`
2. `uv add openai typer rich python-dotenv mcp httpx duckduckgo-search pyyaml`
3. `uv add --dev pytest pytest-asyncio ruff mypy`
4. Set entry point in `pyproject.toml`: `agent = "agent.main:app"`
5. Verify: `uv run agent --help`

---

### Phase 1 — Config and Auth

**Goal:** 4-level settings loaded; API client constructed.

Files: `src/agent/config/settings.py`

- Load and deep-merge the 4 settings levels.
- Construct `openai.AsyncOpenAI(base_url=settings.baseUrl, api_key=os.environ["OPENROUTER_API_KEY"])` client.
- Load `AGENT.md` / `CLAUDE.md` from cwd.
- Load `~/.agent/memory/<project_hash>.json` and append to system prompt.
- Load `.agent/rules/` and resolve which rules apply at startup.

---

### Phase 2 — Event Bus

**Goal:** `EventBus` wired; all subsequent phases emit events.

Files: `src/agent/events/bus.py`, `src/agent/events/types.py`

Deliver `EventBus.on / off / emit`. Define all event type string constants in `types.py`. No listeners yet.

---

### Phase 3 — MCP Client

**Goal:** MCP servers start at session init; their tools appear in Claude's tool list.

Files: `src/agent/mcp/`

1. `MCPClient` wraps the `mcp` Python SDK for one server connection.
2. `MCPManager` starts all configured servers at session start, calls `list_tools()` on each, and merges into the agent tool list with `mcp__<server>__<tool>` namespacing.
3. When `loop.py` dispatches a tool call, `MCPManager.route()` sends it to the right server.
4. Emit `mcp.server_start` and `mcp.tool_call` events.
5. Verify: start `mcp-server-git`, call `mcp__git__git_status`, get a valid response.

---

### Phase 4 — Native Tool Definitions

**Goal:** All native tools defined with schemas and handlers.

Files: `src/agent/tools/`

Native tools (running in-process): `bash`, `read_file`, `write_file`, `edit_file`, `grep_files`, `find_files`.

`web_search` and `web_fetch` are **client-side** tools implemented locally — `duckduckgo-search` for search, `httpx` for URL fetching. Added to the tool list only when `--web` / `"webSearch": true`.

Plugin loader in `registry.py`: scans `./agent_tools/*.py` at startup.

---

### Phase 5 — Agent Loop

**Goal:** Stream → emit events → dispatch tools → loop until `end_turn`.

File: `src/agent/loop.py`

```python
async def run_turn(client, messages, tools, bus, config):
    await bus.emit(Event("turn.start", {"messages": messages}))

    # OpenAI-compatible streaming call (works with OpenRouter, DeepSeek, Claude, etc.)
    stream = await client.chat.completions.create(
        model=config.model,
        messages=messages,          # system prompt is messages[0] with role="system"
        tools=tools,                # OpenAI tool format (see Tool Definitions below)
        stream=True,
        max_tokens=16000,
    )

    tool_calls_acc = []             # accumulate streamed tool call chunks
    assistant_text = ""
    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            assistant_text += delta.content
            await bus.emit(Event("stream.delta", {"text": delta.content}))
        if delta.tool_calls:
            tool_calls_acc = merge_tool_call_chunks(tool_calls_acc, delta.tool_calls)

    finish_reason = chunk.choices[0].finish_reason
    usage = chunk.usage  # present on last chunk when stream_options={"include_usage": True}
    await bus.emit(Event("turn.end", {"usage": usage, "finish_reason": finish_reason}))

    # Append assistant message (text + tool_calls) to history
    assistant_msg = {"role": "assistant", "content": assistant_text or None,
                     "tool_calls": tool_calls_acc or None}
    messages.append(assistant_msg)

    if finish_reason == "tool_calls":
        tool_results = []
        for tc in tool_calls_acc:
            name = tc["function"]["name"]
            input_ = json.loads(tc["function"]["arguments"])

            before = Event("tool.before", {
                "tool_name": name, "tool_id": tc["id"],
                "input": input_, "cancelled": False, "updated_input": None,
            })
            await bus.emit(before)
            if before.data["cancelled"]:
                result_text = "Cancelled by user."
            else:
                effective_input = before.data["updated_input"] or input_
                try:
                    result_text = await dispatch(name, effective_input, mcp_manager)
                except Exception as e:
                    await bus.emit(Event("tool.error", {"tool_name": name, "error": e}))
                    result_text = f"Error: {e}"

            await bus.emit(Event("tool.after", {
                "tool_name": name, "input": effective_input,
                "result": result_text, "duration_ms": elapsed_ms(),
            }))
            # OpenAI tool result format
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": wrap_tool_result(name, result_text),  # injection protection
            })

        messages.extend(tool_results)
        return await run_turn(client, messages, tools, bus, config)

    return messages
```

**OpenAI tool definition format** (used throughout, instead of Anthropic's `input_schema`):

```python
{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from disk and return its contents with line numbers.",
        "parameters": {           # ← "parameters" not "input_schema"
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}
```

**Injection protection wrapper** (applied to every tool result):

```python
def wrap_tool_result(tool_name: str, content: str) -> str:
    return f"<tool_result name=\"{tool_name}\">\n{content}\n</tool_result>"
```

---

### Phase 6 — Plan Mode

**Goal:** Claude proposes a plan before executing; user approves or edits.

File: `src/agent/plan.py`

1. Before the first tool-calling turn, send prompt + `PLAN_SYSTEM_ADDENDUM`.
2. Collect the plan text (text-only response, no tool calls).
3. Emit `plan.proposed`. `DisplayListener` renders the plan and prompts.
4. On approval: emit `plan.approved`, proceed with normal loop.
5. On edit: merge user changes, emit `plan.rejected`, re-propose.
6. On cancel: return to REPL.

Disabled for single-shot `-p` mode and when `"planMode": false`.

---

### Phase 7 — Built-in Listeners

**Goal:** All cross-cutting concerns as listeners; loop stays pure.

Files: `src/agent/events/listeners/`

- **`DisplayListener`**: streaming text via `rich.live.Live`, tool panels, colored diffs.
- **`ApprovalListener`**: `[y/N/all/quit]` for write/bash; auto-approve read-only tools; respects `--no-approval`.
- **`CostTrackerListener`**: accumulates usage; prints summary on `session.end`.
- **`TranscriptListener`**: every event → `~/.agent/transcripts/<session_id>.jsonl`.
- **`PersistenceListener`**: saves/loads conversation history (messages array).
- **`AutoMemoryListener`**: on `session.end`, extracts learnings via Claude, merges into `~/.agent/memory/<hash>.json`.
- **`WebhookListener`**: fire-and-forget POST to configured URL.

---

### Phase 8 — Context Management

**Goal:** Auto-compact when approaching 1M token limit.

File: `src/agent/context.py`

- Track `usage.prompt_tokens` each turn (from the last stream chunk when `stream_options={"include_usage": True}`).
- At 800K tokens: send summarization request, replace history with summary + last 2 turns.
- Emit `context.compact`. `/compact` slash command triggers manually.

---

### Phase 9 — Session Persistence

**Goal:** Conversations saved to disk; `--resume` restores a prior session.

File: `src/agent/session.py`

- Session ID is a short slug (`uuid4()[:8]`), printed at start.
- History at `~/.agent/sessions/<session_id>.json`.
- `--resume <id>` loads and continues.
- `/sessions` lists recent sessions.

---

### Phase 10 — Subagents

**Goal:** Spawn parallel sub-agents for divisible work.

File: `src/agent/subagent.py`

- `spawn_subagent` tool available to Claude: takes a `prompt` string.
- Creates an isolated agent loop with fresh conversation history.
- Multiple subagents run concurrently via `asyncio.gather`.
- Results returned as tool results to the parent agent.
- Emit `subagent.start` / `subagent.end`; display shows nested progress.

---

### Phase 11 — Plugin Tools

**Goal:** Users add tools by dropping Python files into `./agent_tools/`.

File: `src/agent/tools/registry.py` (plugin loader)

Each plugin file exports `SCHEMA: dict` and `async def run(input: dict) -> str`. Registry scans at startup; errors are logged without crashing.

---

### Phase 12 — CLI Entry Point (Final)

**Goal:** All flags, slash commands, and interactive features wired up.

File: `src/agent/main.py`

```
Usage: agent [OPTIONS] [PROMPT]

  Python CLI coding agent. Works with any OpenAI-compatible provider.

Options:
  -p, --prompt TEXT          One-shot prompt (non-interactive)
  --model TEXT               Model to use [default: deepseek/deepseek-chat-v3-0324]
  --base-url TEXT            OpenAI-compatible API base URL [default: https://openrouter.ai/api/v1]
  --no-approval              Skip all approval prompts
  --no-plan                  Skip plan mode
  --web                      Enable web search
  --system-file PATH         Custom system prompt file
  --resume SESSION_ID        Resume a previous session
  --list-sessions            List recent sessions
  --webhook-url URL          POST events to this URL
  --version                  Show version and exit
  --help                     Show this message and exit
```

**Slash commands:**

| Command | What it does |
|---|---|
| `/exit` | End the session |
| `/clear` | Reset conversation history (keep system prompt) |
| `/compact` | Manually trigger context compaction |
| `/cost` | Show token usage and estimated cost for this session |
| `/plan <task>` | Enter plan mode for the given task |
| `/init` | Generate an `AGENT.md` for the current project |
| `/memory` | View and edit auto-memory for this project |
| `/mcp` | List connected MCP servers and their tools |
| `/sessions` | List recent sessions |
| `/resume <id>` | Resume a session by ID |

---

## Key Implementation Notes

### Streaming + Tool Use

Use `client.messages.stream()` and `.get_final_message()`. Never dispatch tool calls mid-stream. Execute all `tool_use` blocks in the completed response, then loop.

### Parallel Tool Calls

Claude may return multiple `tool_use` blocks. Execute independent ones concurrently via `asyncio.gather`; return all results in one `user` message.

### MCP Tool Routing

Tool names with the prefix `mcp__<server>__` are routed to `MCPManager.call(server, tool, input)`. All other names go to the native tool registry. The loop doesn't distinguish — dispatch is transparent.

### Error Handling

```python
from openai import RateLimitError, APIConnectionError, APIStatusError

except RateLimitError as e:
    sleep(int(e.response.headers.get("retry-after", 30))); retry()
except APIConnectionError:
    sleep(5); retry()
except APIStatusError as e:
    await bus.emit(Event("error", {"error": e})); break
```

### Security

- Never `eval()` tool parameters.
- Sandbox `write_file` to cwd; reject `..` and absolute paths outside cwd.
- Warn on bash commands containing `rm -rf`, `sudo`, `curl | sh`.
- Plugin tools run in-process — no sandbox isolation. Document clearly.

---

## Development Milestones

| Milestone | Deliverable | Success Criteria |
|-----------|-------------|-----------------|
| M0 | Scaffolding | `uv run agent --help` works |
| M1 | Config + Auth | 4-level settings load; agent authenticates; memory + rules injected |
| M2 | Event bus | `EventBus` wired; events emit without listeners |
| M3 | MCP client | `mcp-server-git` starts; `git_status` callable |
| M4 | Native tools | All 6 native tools defined with schemas and handlers |
| M5 | Agent loop | Agent reads, edits, writes a file; all events fire |
| M6 | Plan mode | Claude proposes a plan; user approves before execution begins |
| M7 | Built-in listeners | Display, approval, cost tracker, transcript all wired |
| M8 | Context mgmt | Long conversations auto-compact; `context.compact` fires |
| M9 | Session persistence | Conversations saved; `--resume` works |
| M10 | Auto memory | Learnings extracted post-session; injected next session |
| M11 | Subagents | Parent spawns parallel sub-agents; results aggregated |
| M12 | Plugin tools | Custom tool from `./agent_tools/` is callable |
| M13 | Web search | `--web` adds web search; agent looks up docs |
| M14 | Polish | Webhook, scoped rules, all slash commands, full test suite |

---

## Getting Started (Once Implementation Begins)

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Scaffold
uv init my-coding-agent --package
cd my-coding-agent

# Dependencies
uv add openai typer rich python-dotenv mcp httpx duckduckgo-search pyyaml
uv add --dev pytest pytest-asyncio ruff mypy

# Git MCP server (run once to cache)
uvx mcp-server-git --help

# Run the agent
uv run agent
```
