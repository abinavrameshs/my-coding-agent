# Glossary

Project-specific terms and the acronyms used across the codebase and docs. Terms are
**bold** when defined.

---

## A–C

**Agent loop** — `loop.run_turn`. The function that streams from the LLM, executes
tool calls, and recurses until the model stops requesting tools. The only place the
LLM SDK is called for normal turns.

**Approval mode** — the permission policy in `Config.approval_mode` controlling which
tool calls are auto-approved vs. prompted. Values: `default`, `acceptEdits`, `auto`,
`bypassPermissions`. Enforced by `ApprovalListener`.

**Compaction** — summarising old conversation turns into a single summary block when
the context grows past 80% of `Config.context_limit`. Implemented in
`context.compact_messages`; emits `context.compact`.

**Context block** — an element of the system prompt list: `{"type": "text", "text": …}`,
optionally with `cache_control`. Assembled by `memory.loader.assemble_system_prompt`.

**CSV** / see **tool_call_id**.

**cwd** — the project working directory. Tools resolve relative paths against it;
`files._safe_path` rejects anything that escapes it.

---

## D–H

**Dispatch** — `ToolRegistry.dispatch` / `_run`. The single funnel that routes a tool
name to a handler (native, plugin, or MCP) and wraps the result.

**Event bus** — `events/bus.EventBus`. Delivers typed events to listeners in
registration order. See [`events-and-listeners.md`](events-and-listeners.md).

**Event type constant** — a string like `TOOL_BEFORE` in `events/types.py`. Import
these; never hardcode event name strings.

**Fail-soft** — a design property of JEV calls and several listeners: on error they
return a safe default instead of raising, so infrastructure failures never block a
turn.

**Hint** — the last user message, passed to `registry.schemas_for(hint)` to filter MCP
servers by keyword relevance.

---

## J–M

**JEV** — the lightweight **decisions model** (`~typesafe/jev-latest`) used for cheap
boolean decisions without text generation: tool-group routing, bash-safety, memory
worthiness, and memory-intent detection. Lives in `routing.py`. "noul" is the
probability field compared against `jev_threshold`.

**Listener** — an object with a `register(bus)` method that subscribes handlers to
event types. Built-ins live in `events/listeners/`. Registered in `repl.py`, never in
`loop.py`.

**MCP (Model Context Protocol)** — the protocol used to connect external tool servers.
Clients: `StdioMCPClient` / `HttpMCPClient`; lifecycle: `MCPManager`.

**Memory (auto-memory)** — persistent `{key: value}` facts in `.agent/memory.json`,
injected into the system prompt as "## Remembered context" and updated by explicit
`remember` tool use, inline intent detection, or end-of-session extraction.

**Mutation contract** — the convention that listeners may write `cancelled`,
`updated_input`, or `updated_plan` into `event.data`, and the emitter reads them back
after `await bus.emit(...)`.

---

## P–S

**Plan mode** — a two-phase protocol where the model first outputs `<plan>…</plan>`
with no tool calls, the user approves/edits via `PlanListener`, then execution proceeds.
Gated by `Config.plan_mode`.

**Plugin tool** — a tool loaded at runtime from `./agent_tools/<name>.py` exporting
`SCHEMA` + async `run`. Not a tool group; always included.

**Progressive disclosure** — the documentation principle used here: keep the always-
loaded `AGENT.md` short and push detail into on-demand `docs/agent_docs/*.md`.

**Scoped rule** — a `.agent/rules/*.md` file with optional `globs:` frontmatter,
injected into the system prompt only when the project contains matching files.

**Skill** — a Markdown prompt (`.agent/skills/<name>/SKILL.md` or
`.agent/commands/<name>.md`) invoked by `/name`. Supports `` !`cmd` `` shell injection.

**Slash command** — a `/…` input handled inline by `repl.py` (`/help`, `/tools`,
`/memory`, `/mcp`, `/skills`, …) or dispatched to a skill.

**Subagent** — an isolated agent loop spawned by the `spawn_subagent` tool
(`subagent.py`), with its own event bus and message history, max depth 3.

**Parallel batch** — a set of plan items run concurrently by `spawn_parallel`.
Items are grouped into dependency levels by `parallel.plan_batches`, capped at
`cfg.max_parallel_subagents`, and split further by `group_by_files` so two tasks
touching the same file never run together. Emits `parallel.batch_start`/`_end`.

**System prompt** — the `role="system"` message assembled from base instructions, plan
instructions, `AGENT.md`, scoped rules, and memory.

---

## T–Z

**Tool group** — `ToolGroup` enum: `CORE` (always on), `WEB`, `GIT`, `MCP`. Controls
which tool schemas are sent to the model.

**Tool result wrapper** — `<tool_result name="…">…</tool_result>` applied by
`wrap_tool_result`. Prompt-injection defence.

**tool_call_id** — the OpenAI identifier tying a `role="tool"` result message back to
the assistant's `tool_calls` entry. Must be preserved or the next request 400s.

**Transcript** — a JSONL log of every event, one per line, at
`.agent/transcripts/<session_id>.jsonl`.

**Turn** — one `run_turn` invocation: from `turn.start` to `turn.end`. A user message
may cause several turns (because of tool-call recursion).

---

## File/config quick reference

| Name | What |
|---|---|
| `AGENT.md` | Project instructions, injected into every session (falls back to `CLAUDE.md`) |
| `.agent/settings.json` | Project config (committed) |
| `.agent/settings.local.json` | Personal config overrides (gitignored) |
| `.agent/.mcp.json` | MCP server config (wins over settings `mcpServers`) |
| `.agent/memory.json` | Persistent `{key: value}` facts |
| `.agent/rules/*.md` | Scoped rules |
| `.agent/skills/*/SKILL.md` | Skills |
| `.agent/commands/*.md` | Flat commands (override skills) |
| `.agent/sessions/*.json` | Saved conversations |
| `.agent/transcripts/*.jsonl` | Event logs |
| `agent_tools/*.py` | Plugin tools |

## See also

- [`../README.md`](README.md) — doc index
- [`architecture.md`](architecture.md), [`configuration.md`](configuration.md)