# my-coding-agent

A terminal coding agent (Claude-Code-style) built from scratch in Python. It streams
a model's responses, executes tools (files, bash, web, git, MCP), and reacts to a
typed event bus. It talks to **any OpenAI-compatible provider** (default: OpenRouter)
via the `openai` SDK.

> This file is loaded into **every** agent session (see `src/agent/memory/loader.py`).
> Keep it short. For depth, read the topic files under `docs/agent_docs/`.

## Stack

- Python ≥ 3.11, packaged with **uv** (`pyproject.toml`, entry point `agent = agent.main:app`)
- Libraries: `typer` (CLI), `rich` (terminal UI), `openai` ≥ 1.0 (LLM SDK), `pydantic` v2 (config), `mcp` (MCP client), `httpx`, `pyyaml`, `ddgs` (web search)
- Tests: `pytest` with `asyncio_mode = "auto"`. Lint: `ruff` (line length 120). Types: `mypy` (non-strict).

## Commands

```bash
uv run agent                 # interactive REPL
uv run agent -p "..."        # single-shot, non-interactive
uv run pytest                # run tests
uv run ruff check src        # lint
uv run mypy src              # type check
```

## Architecture map (where things live)

| Path | Responsibility |
|---|---|
| `src/agent/main.py` | Typer CLI entry point; parses flags → builds `Config` → calls `run_repl` |
| `src/agent/repl.py` | Interactive loop, slash commands, wires all listeners + MCP at startup |
| `src/agent/loop.py` | **The agent loop.** Only module that calls the LLM SDK. Streams, dispatches tools, recurses |
| `src/agent/routing.py` | "JEV" decisions model: tool-group routing, bash-safety, memory intent, plan dependency classification |
| `src/agent/context.py` | Conversation compaction (summarise old turns) |
| `src/agent/session.py` | Session IDs + `.agent/` storage paths |
| `src/agent/subagent.py` | Spawn isolated sub-agents (`spawn_subagent`) and batched parallel tasks (`spawn_parallel`) |
| `src/agent/parallel.py` | Pure scheduling helpers: dependency batches, width cap, file-conflict serialisation |
| `src/agent/tools/registry.py` | **Tool registry.** Builds schemas, groups, dispatch, injection-wrapping |
| `src/agent/tools/*.py` | Native tool handlers (files, bash, web, todo, memory) |
| `src/agent/events/` | `EventBus` + `types.py` constants + `listeners/` |
| `src/agent/mcp/` | MCP client (stdio/HTTP) + manager (start/route/stop servers) |
| `src/agent/config/config.py` | `Config` (pydantic) + 3-level settings merge |
| `src/agent/memory/` | System-prompt assembly, skills, auto-memory extraction |

## Consistency checks (required before closing any task)

Before marking a task done, run these three checks:

1. **New or renamed method on a class** — grep for every other class that has a
   method with the same name and verify each is updated consistently.
   Example: adding `schemas_for()` to `ToolRegistry` → grep `def schemas_for` →
   find `_SubagentRegistry` → confirm it also implements the method.

2. **New event payload key** — grep for every other emitter of that event type and
   verify all use the same key names. Example: emitting `ErrorPayload(error=...)` →
   grep `emit.*ERROR` → confirm no emitter still uses `{"message": "..."}`.
   Use the typed payload models in `events/payloads.py` — a wrong field name is a
   `ValidationError` at construction time, not a silent runtime miss.

3. **Integration smoke** — run at least one call through the actual path end-to-end
   before declaring done. Unit tests verify units; this catches disconnected wiring.
   For tool changes: `uv run pytest tests/test_subagent_integration.py -v`.

## Rules (do not violate)

- **Only `loop.py` calls the LLM SDK.** Don't create `AsyncOpenAI` elsewhere except
  `memory/auto.py` (extraction) and `context.py` (summarisation), which are deliberate
  one-shot helpers.
- **`loop.py` never imports from `events/listeners/`.** It only `emit`s on the bus.
  All cross-cutting concerns (display, approval, cost, persistence, memory) are listeners.
- **Add tools in `tools/registry.py`'s dispatch** or as a plugin in `agent_tools/`.
  Do not add tool branches anywhere else.
- **Git goes through MCP** (`mcp__git__*` tools), never a bespoke subprocess handler.
- **Listeners may mutate `event.data`** (`cancelled`, `updated_input`, `updated_plan`).
  The loop reads those mutations back *after* `await bus.emit(...)`.
- **Settings keys are camelCase** in JSON but `Config` accepts snake_case too (aliases).
- **Tool output is wrapped** in `<tool_result name="...">…</tool_result>` to resist
  prompt injection. Never unwrap it before returning to the model.
- **Parallel planning:** independent plan items are run concurrently via
  `spawn_parallel`; items that depend on another (or edit the same file) are
  serialised. Scheduling logic lives in `parallel.py` (pure, no I/O) and is
  fail-soft — if dependency classification fails, everything runs sequentially.

## Extension points (summary)

| Want to… | Do this | Details |
|---|---|---|
| Add a native tool | Handler + schema in `tools/`, register in `registry.py` | `docs/agent_docs/adding-a-tool.md` |
| Add a plugin tool | Drop `agent_tools/<name>.py` exporting `SCHEMA` + async `run` | same |
| Add a skill / command | `.agent/skills/<name>/SKILL.md` or `.agent/commands/<name>.md` | `docs/agent_docs/memory-and-skills.md` |
| Add an MCP server | `.agent/.mcp.json` or settings `mcpServers` | `docs/agent_docs/mcp-integration.md` |
| Add a listener | Subscribe on the bus in `repl.py` | `docs/agent_docs/events-and-listeners.md` |
| Change config | `Config` in `config/config.py` + `.agent/settings.json` | `docs/agent_docs/configuration.md` |

## Where to read next

- **`docs/agent_docs/README.md`** — index of all deep-dive docs
- **`docs/agent_docs/architecture.md`** — data flow and module boundaries
- **`docs/agent_docs/tool-system.md`** — tool groups, schema filtering, dispatch
- **`docs/agent_docs/events-and-listeners.md`** — event catalog + mutation contract
- **`docs/agent_docs/mcp-integration.md`** — MCP transports and namespacing
- **`docs/agent_docs/configuration.md`** — settings hierarchy, all `Config` fields, JEV
- **`docs/agent_docs/memory-and-skills.md`** — memory, skills, system-prompt assembly
- **`docs/agent_docs/testing.md`** — how to run and add tests
- **`docs/agent_docs/glossary.md`** — project-specific terms

> ⚠️ `docs/PLAN.md` is the **original design document** and is now **partly stale**
> (it predates the OpenAI-SDK switch, the JEV router, tool groups, and skills). Treat
> this `AGENT.md` and `docs/agent_docs/` as the source of truth.