# Architecture

How `my-coding-agent` is put together: the layers, the data flow, the module
boundaries, and the invariants that keep it coherent.

- **Audience:** an agent or developer about to modify the code.
- **Prerequisite:** read [`../AGENT.md`](../AGENT.md) first (the map + rules).

---

## 1. The big picture

The agent is a **streaming tool-use loop** wrapped in an **event bus**.

```
┌───────────────────────────────────────────────────────────────────────────┐
│ main.py  (Typer CLI)                                                       │
│   parses flags → merges into Config → asyncio.run(run_repl(cfg))           │
└───────────────────────────────┬───────────────────────────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ repl.py  (session orchestrator)                                            │
│   • builds EventBus, registers all listeners                                │
│   • starts MCP servers (MCPManager)                                         │
│   • builds ToolRegistry (native + plugins + MCP tools)                      │
│   • assembles the system prompt (memory/loader.py)                          │
│   • reads user input, dispatches slash commands, calls run_turn             │
└───────┬───────────────────────────────────────────────────┬───────────────┘
        │ handle_turn(user_input)                            │ slash cmds
        ▼                                                    ▼
┌───────────────────────┐                          ┌───────────────────────┐
│ routing.py (JEV)      │  decides tool groups     │ repl handles /tools,   │
│  auto_enable_groups   │                          │ /memory, /mcp, /skills │
└───────────┬───────────┘                          └───────────────────────┘
            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ loop.py  run_turn()   ← THE ONLY LLM CALL SITE                              │
│   emit turn.start                                                           │
│   stream = client.chat.completions.create(..., tools=registry.schemas_for())│
│   async for chunk: emit stream.delta ; accumulate tool_call chunks          │
│   append assistant message → emit message.assistant                         │
│   if <plan> block and plan_mode: emit plan.proposed → await approval        │
│   for each tool call (parallel): emit tool.before → dispatch → tool.after   │
│   recurse run_turn() until no tool calls remain                             │
└───────┬───────────────────────────────────────────┬───────────────────────┘
        │ emit events                                │ registry.dispatch()
        ▼                                            ▼
┌───────────────────────────┐            ┌───────────────────────────────────┐
│ events/bus.py             │            │ tools/registry.py                 │
│  delivers to listeners    │            │  native · plugin · MCP dispatch   │
└───────┬───────────────────┘            └───────────────────────────────────┘
        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ events/listeners/*                                                          │
│  display · approval · cost_tracker · transcript · persistence ·             │
│  auto_memory · webhook · plan_listener                                      │
└───────────────────────────────────────────────────────────────────────────┘
```

**Reading order for newcomers:** `main.py` → `repl.py` → `loop.py` → `tools/registry.py`
→ `events/bus.py`. Everything else hangs off those five.

---

## 2. Layers and dependency direction

```
main.py
  └── repl.py
        ├── loop.py                 (the LLM loop)
        │     ├── config/config.py  (Config)
        │     ├── events/bus.py     (emit only)
        │     └── tools/registry.py (schemas_for + dispatch)
        ├── routing.py              (JEV: tool routing, approval, memory intent)
        ├── context.py              (compaction)
        ├── subagent.py             (spawn sub-agents + parallel batches)
        ├── parallel.py             (pure dependency/file batching — no I/O)
        ├── mcp/manager.py          (MCP lifecycle + routing)
        └── memory/loader.py        (system prompt)

events/listeners/*   — subscribe on the bus; NEVER imported by loop.py
config/config.py     — read-only after startup
memory/*, context.py — helper modules, not part of the loop
```

### Hard rules

1. **`loop.py` is the only module that calls the LLM SDK.**
   - Exceptions: `memory/auto.py` (fact extraction) and `context.py` (summarisation)
     each build a throwaway `AsyncOpenAI` client for a single non-streaming call.
     These are intentional, self-contained helpers — keep them that way. Do **not**
     add more.
   - `loop.make_client(cfg)` is the canonical factory for the streaming client.

2. **`loop.py` never imports from `events/listeners/`.**
   - The loop knows about `EventBus`, not about Display, Approval, Cost, etc.
   - All side-effects (rendering, prompting, logging, persistence, memory) are
     listeners registered in `repl.py`. See `events-and-listeners.md`.

3. **Tools never import from `loop.py`.**
   - `tools/` and `mcp/` depend on `Config` and their own helpers only.
   - Keeps tools unit-testable without a client or event bus.

4. **`config/` is immutable after startup.**
   - `load_config()` runs once in `main.py` (and again in tests). Nothing mutates
     `Config` mid-session.

5. **`routing.py` failures are always non-fatal.**
   - Every JEV call returns a safe fallback (`{}` / `False`) on error so a flaky
     decisions endpoint never blocks a turn.

---

## 3. A turn, end to end

Walking one user message through the system (interactive mode):

1. **`repl.py`** reads the line. If it starts with `/`, it's a slash command
   handled inline (see `memory-and-skills.md`). Otherwise → `handle_turn(input)`.

2. **`handle_turn`** runs two JEV checks in parallel (both time-boxed by
   `cfg.jev_timeout`, both non-fatal):
   - `routing.auto_enable_groups(...)` — asks JEV which tool groups this request
     needs (git / web / mcp) and silently enables them on the registry, printing
     `⚡ auto-enabled: …`.
   - `has_memory_intent(...)` — if the message looks like "remember X" / "my name
     is Y", extracts facts and writes them via the `remember` tool.

3. The user message is appended to `messages`, then **`loop.run_turn(...)`** is called.

4. **`run_turn`** emits `turn.start`, then:
   - Pulls the last user message as a **relevance hint** for MCP tool filtering.
   - Calls `client.chat.completions.create(..., tools=registry.schemas_for(hint))`
     with `stream=True`, wrapped in a retry loop for `RateLimitError`,
     `APIConnectionError`, `APIStatusError` (up to `cfg.max_retries`).
   - Streams chunks: text deltas → `stream.delta` events; tool-call deltas are
     accumulated by `_merge_tool_call_chunks`. Usage (incl. cached tokens) is
     captured from the final chunk.

5. The assistant message (text + any `tool_calls`) is appended to `messages`, and
   **`message.assistant`** is emitted.

6. **Plan gate.** If `cfg.plan_mode` and the text contains a `<plan>…</plan>` block
   (`_extract_plan`), the loop emits **`plan.proposed`** with `cancelled=False`.
   - A listener (PlanListener) may set `cancelled=True` → the turn ends.
   - It may set `updated_plan` → `plan.approved` is emitted with the edited text.
   - If the model emitted **no tool calls**, the loop injects
     `"Plan approved. Please proceed with execution."` and recurses with
     `_check_plan=False`.
   - If the model *did* include tool calls alongside the plan (fallback path), the
     loop falls through and executes them after approval.

7. **Tool execution.** If there are tool calls:
   - Each call runs in `execute_tool`, all in parallel via `asyncio.gather`.
   - Per call: **`tool.before`** is emitted; listeners may set `cancelled=True`
     (skip) or `updated_input` (rewrite args). Then `registry.dispatch(name, args)`
     runs, and **`tool.after`** is emitted with output + `duration_ms`.
   - Results become `{"role": "tool", "tool_call_id": ..., "content": ...}` messages.
   - `run_turn` **recurses** (with `_check_plan=False`) until the model stops
     requesting tools.

8. Back in `handle_turn`, after `run_turn` returns, `repl.py` checks
   `_last_usage["prompt_tokens"]`; if it exceeds **80% of `cfg.context_limit`** it
   calls `_do_compact()`, which summarises old turns via `context.compact_messages`
   and emits **`context.compact`**.

> **Gotcha — usage capture.** `repl.py` registers a small handler on `turn.end`
> (`_capture_usage`) to stash the latest usage dict in `_last_usage`. This is *not*
> a listener class — it's a closure. Don't remove it; auto-compaction depends on it.

---

## 4. The agent loop (`loop.py`) in detail

### `make_client(cfg) -> AsyncOpenAI`
Builds the shared client: `base_url=cfg.base_url`, `api_key=OPENROUTER_API_KEY`
(falling back to `"no-key"`), plus `HTTP-Referer` / `X-Title` headers.

### `_merge_tool_call_chunks(acc, deltas) -> list[dict]`
OpenAI streams tool calls in fragments (an `index`, an `id`, a `function.name`
chunk, then `function.arguments` chunks). This accumulates them into complete
`{"id","type","function":{"name","arguments"}}` dicts, padding gaps by index.

### `run_turn(client, messages, cfg, bus, registry, _check_plan=True) -> None`
- **Mutates `messages` in place.** It does not return the history — callers keep the
  same list reference.
- Retry loop: `RateLimitError` honours `retry-after`; `APIConnectionError` waits 5s;
  `APIStatusError` gives up immediately. All emit `error`.
- `streaming`: text → `stream.delta`; tool deltas accumulated; usage tracked.
- Plan handling: see §3 step 6.
- Tool execution: `asyncio.gather` over `execute_tool` closures (parallel).
- Recursion: tails-call itself until the model stops calling tools.

> **Rule — never dispatch tools mid-stream.** Tool calls are only executed *after*
> the stream is fully consumed and the assistant message is recorded. Mid-stream
> dispatch corrupts the OpenAI message sequence.

> **Gotcha — tool ID preservation.** `execute_tool` returns the tool result keyed by
> `tc["id"]`. OpenAI requires every `role="tool"` message to carry the matching
> `tool_call_id`, or the next request 400s.

---

## 5. Module reference (concise)

| Module | Public surface | Notes |
|---|---|---|
| `main.py` | `app` (Typer), `main()` | Builds CLI overrides; `--list-sessions` short-circuits before REPL |
| `repl.py` | `run_repl(cfg, initial_prompt, resume_id)`, `HELP_TEXT` | Wires listeners; owns slash commands; owns compaction trigger |
| `loop.py` | `make_client`, `run_turn` | LLM streaming + tool dispatch + plan gate |
| `routing.py` | `JEVClient`, `auto_enable_groups`, `should_approve_bash`, `should_extract_memory`, `has_memory_intent` | All JEV decisions; all fail-soft |
| `context.py` | `compact_messages(messages, cfg, client)` | Keeps system msgs + last 2 turn pairs; replaces the rest with a summary |
| `session.py` | `new_session_id`, `sessions_dir`, `transcripts_dir` | IDs are `uuid4().hex[:8]`; paths under `.agent/` |
| `subagent.py` | `SCHEMA`, `PARALLEL_SCHEMA`, `run_subagent`, `run_parallel`, `_SubagentRegistry` | Max depth 3; isolates bus + messages; intercepts `spawn_subagent` / `spawn_parallel` |
| `parallel.py` | `normalize_deps`, `has_cycle`, `plan_batches`, `group_by_files` | Pure scheduler: dependency batches, width cap, file-conflict serialisation |
| `tools/registry.py` | `ToolRegistry`, `RegistryProtocol`, `ToolGroup`, `wrap_tool_result` | Schema build/cache, group filtering, dispatch; `RegistryProtocol` is the structural interface `run_turn` accepts |
| `events/bus.py` | `Event`, `EventBus` | `on/off/emit`; wildcard; sequential delivery |
| `events/types.py` | event constants | Single source of truth for event names |
| `events/payloads.py` | Pydantic payload models | One typed `BaseModel` per event type; wrong field names fail at construction |
| `config/config.py` | `Config`, `MCPServerConfig`, `load_config`, `deep_merge` | Pydantic; camelCase aliases; 3-level merge |
| `memory/loader.py` | `assemble_system_prompt`, `system_prompt_text` | Base + project + rules + memory blocks, with cache marker |
| `memory/skills.py` | `Skill`, `discover_skills`, `BUILTIN_SKILLS` | SKILL.md / command discovery + shell injection |
| `memory/auto.py` | `extract_and_save`, `extract_from_message`, `get_memories`, … | Auto-memory read/write |
| `mcp/client.py` | `StdioMCPClient`, `HttpMCPClient`, `MCPClient` (alias) | One background task per server |
| `mcp/manager.py` | `MCPManager` | Start/stop, tool list, routing, `${VAR}` expansion |

---

## 6. Concurrency model

- The whole runtime is **asyncio**. `asyncio.run(run_repl(...))` is the single entry.
- **Tool calls within one turn run concurrently** (`asyncio.gather` in `loop.run_turn`).
- **Parallel plan items run concurrently** — `spawn_parallel` batches independent
  tasks (dependency levels, capped at `cfg.max_parallel_subagents`) and runs each
  batch with `asyncio.gather`; `parallel.py` holds the pure scheduling policy.
- **MCP servers start concurrently** (`asyncio.gather` in `MCPManager.start_all`),
  turning startup time from `sum(latencies)` into `max(latencies)`.
- **JEV checks run concurrently** with each other in `handle_turn` (`asyncio.gather`).
- **Interactive input runs on an executor thread** (`run_in_executor` over
  `console.input`) so it doesn't block the event loop.
- **Webhooks fire-and-forget** (`asyncio.create_task`) — delivery never blocks a turn.

> **Gotcha — listener handlers are sequential, not concurrent.** `EventBus.emit`
> awaits each handler in registration order. This is deliberate: it makes
> `cancelled` / `updated_input` mutation predictable. If you need parallelism inside
> a handler, spawn tasks inside it — don't rely on the bus.

---

## 7. Error-handling philosophy

| Failure | Behaviour |
|---|---|
| LLM rate limit / connection | Retried up to `cfg.max_retries`, then `error` event |
| LLM other HTTP error | `error` event, turn ends |
| Tool raises | Caught in `registry.dispatch` → returned as `"Error: …"` text to the model |
| Plugin import fails | Logged, plugin skipped, agent continues |
| MCP server fails to start | `error` event with a hint; other servers still start |
| JEV call fails | Silent fallback (safe default) |
| Transcript/webhook write fails | Swallowed — never crashes the agent |
| `Ctrl-C` mid-turn | `KeyboardInterrupt` caught in `handle_turn`; the dangling user msg is popped |

The principle: **the model should almost always get *something* back as a tool
result**, and infrastructure failures must never take down the REPL.

---

## 8. Key files

- `src/agent/main.py`, `src/agent/repl.py`, `src/agent/loop.py`
- `src/agent/tools/registry.py`, `src/agent/events/bus.py`
- `src/agent/routing.py`, `src/agent/context.py`, `src/agent/subagent.py`

## See also

- [`tool-system.md`](tool-system.md) — registry, groups, dispatch
- [`events-and-listeners.md`](events-and-listeners.md) — the event contract
- [`mcp-integration.md`](mcp-integration.md) — MCP servers
- [`configuration.md`](configuration.md) — every config field