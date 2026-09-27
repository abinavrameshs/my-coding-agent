# Events and listeners

The event bus is how the agent loop stays decoupled from everything that observes
it. This doc covers the bus mechanics, the full event catalog, the **mutation
contract**, and every built-in listener.

- **Prerequisite:** [`architecture.md`](architecture.md).
- **Files:** `src/agent/events/bus.py`, `src/agent/events/types.py`,
  `src/agent/events/listeners/*.py`.

---

## 1. Why an event bus

`loop.py` emits typed events at every lifecycle point. Listeners subscribe and react.
The loop has **no knowledge of what listeners exist** — it only knows the bus. This
means display, approval, cost tracking, persistence, memory, and webhooks are all
**pluggable side-effects** rather than hardcoded steps in the loop.

```
loop.py ──emit──► EventBus ──► DisplayListener
                            ──► ApprovalListener
                            ──► CostTrackerListener
                            ──► TranscriptListener  (wildcard)
                            ──► PersistenceListener
                            ──► AutoMemoryListener
                            ──► WebhookListener      (wildcard)
                            ──► PlanListener
```

---

## 2. The bus (`events/bus.py`)

```python
@dataclass
class Event:
    type: str
    data: BaseModel = field(default_factory=EmptyPayload)

class EventBus:
    def on(self, event_type: str, handler: Handler) -> None: ...
    def off(self, event_type: str, handler: Handler) -> None: ...
    async def emit(self, event: Event) -> Event: ...
```

`Event.data` is a Pydantic `BaseModel` — always a typed payload model from
`events/payloads.py`. The default is `EmptyPayload` (for events that carry no data).

### Delivery order (important)
`emit` calls, **in registration order**:
1. all handlers registered for `event.type`
2. then all handlers registered for the **wildcard** `"*"` (unless the event *is* `"*"`)

Handlers may be sync or async (`_call` awaits if the result is awaitable).

### `emit` returns the event
Because it returns the (possibly mutated) event, callers can inspect changes inline:

```python
from agent.events.payloads import ToolBeforePayload
event = await bus.emit(Event(TOOL_BEFORE, ToolBeforePayload(tool="bash", input={...})))
if event.data.cancelled:
    ...  # a listener vetoed this call
```

> **Rule — sequential, not concurrent.** Handlers run one after another, awaited in
> turn. This is what makes mutation predictable: each handler sees the changes made
> by the previous one. Do not change this to `gather`.

> **Gotcha — exceptions in a handler propagate.** `emit` does not swallow handler
> exceptions. Keep listeners defensive (most built-ins wrap risky work in
> `try/except`). A throwing listener will abort the emitting code path.

---

## 3. The mutation contract

Events carry a mutable Pydantic `data` model (from `events/payloads.py`). Certain
event types define fields that the **emitter reads back after `emit` returns** — this
is how listeners influence control flow without the loop importing them.

| Event | Listener may set | Effect |
|---|---|---|
| `tool.before` | `event.data.cancelled = True` | Skip execution; model gets "Cancelled by user." |
| `tool.before` | `event.data.updated_input = {...}` | Replace the tool arguments before dispatch |
| `plan.proposed` | `event.data.cancelled = True` | Abort the turn (plan rejected) |
| `plan.proposed` | `event.data.updated_plan = "..."` | Use the edited plan text in `plan.approved` |

This is the mechanism behind "hooks" and "hook input modification".

```python
# loop.py (tool.before)
before = await bus.emit(Event(TOOL_BEFORE, ToolBeforePayload(
    tool=name, input=args, call_id=id,
)))
if before.data.cancelled:
    content = "<tool_result ...>Cancelled by user.</tool_result>"
else:
    if before.data.updated_input is not None:
        args = before.data.updated_input
    content = await registry.dispatch(name, args)
```

> **Gotcha — `updated_input` is checked with `is not None`, not truthiness.** Setting
> it to `{}` (empty dict) is a valid rewrite. Always use
> `if event.data.updated_input is not None`.

---

## 4. Event catalog

Constants live in `events/types.py` — always import them, never hardcode strings.

| Constant | String | Emitted when | Key `data` fields | Mutable |
|---|---|---|---|---|
| `SESSION_START` | `session.start` | `repl.py` after wiring | `model`, `session_id` | — |
| `SESSION_END` | `session.end` | REPL exit / single-shot end | (none) | — |
| `TURN_START` | `turn.start` | top of `run_turn` | `message_count` | — |
| `TURN_END` | `turn.end` | end of each `run_turn` | `usage` | — |
| `STREAM_DELTA` | `stream.delta` | each text chunk | `text` | — |
| `MESSAGE_ASSISTANT` | `message.assistant` | after stream completes | `text`, `usage` | — |
| `TOOL_BEFORE` | `tool.before` | before each tool dispatch | `tool`, `input`, `call_id`, `cancelled` | ✅ `cancelled`, `updated_input` |
| `TOOL_AFTER` | `tool.after` | after each tool dispatch | `tool`, `call_id`, `output`, `duration_ms` | — |
| `TOOL_APPROVAL` | `tool.approval_requested` | (reserved) | — | — |
| `PLAN_PROPOSED` | `plan.proposed` | `<plan>` block detected | `plan`, `cancelled` | ✅ `cancelled`, `updated_plan` |
| `PLAN_APPROVED` | `plan.approved` | plan accepted | `plan` | — |
| `PLAN_CANCELLED` | `plan.cancelled` | (reserved) | — | — |
| `CONTEXT_COMPACT` | `context.compact` | compaction runs | `tokens_before`, `msgs_before`, `msgs_after` | — |
| `SUBAGENT_START` | `subagent.start` | sub-agent spawned | `subagent_id`, `prompt` | — |
| `SUBAGENT_END` | `subagent.end` | sub-agent finished | `subagent_id`, `result` | — |
| `PARALLEL_BATCH_START` | `parallel.batch_start` | a `spawn_parallel` batch begins | `tasks` (list of ids) | — |
| `PARALLEL_BATCH_END` | `parallel.batch_end` | a `spawn_parallel` batch finishes | `tasks` (list of ids) | — |
| `MCP_SERVER_START` | `mcp.server_start` | one server started | `server`, `tools`, `transport` | — |
| `MCP_READY` | `mcp.ready` | all servers attempted | `servers` (name→count), `total_tools` | — |
| `MCP_TOOL_CALL` | `mcp.tool_call` | (reserved) | — | — |
| `ERROR` | `error` | any unhandled error | `error` | — |
| `WILDCARD` | `*` | n/a (subscription key) | — | — |

> **Note — the PLAN.md catalog is out of date.** The old plan listed events like
> `message.user`, `tool.approved`, `tool.denied`, `tool.error`, `memory.write`,
> `session.save`, `session.load`. Those are **not emitted** in the current code.
> `events/types.py` is the source of truth.

---

## 5. Built-in listeners

Each listener is a small class with a `register(bus)` method that subscribes its
handlers. They are all instantiated and registered in `repl.run_repl`.

| Listener | Subscribes to | Responsibility |
|---|---|---|
| `DisplayListener` | `stream.delta`, `message.assistant`, `tool.before`, `tool.after`, `turn.end`, `context.compact`, `subagent.start/end`, `parallel.batch_start/end`, `mcp.ready`, `error` | Renders everything to the terminal with Rich |
| `ApprovalListener` | `tool.before`, `session.start` | Gates destructive tools by permission mode; may set `cancelled` |
| `CostTrackerListener` | `session.start`, `turn.end`, `session.end` | Accumulates tokens; prints cost summary on exit |
| `TranscriptListener` | `*` (wildcard) | Appends every event as a JSON line to `.agent/transcripts/<id>.jsonl` |
| `PersistenceListener` | `turn.end` | Saves the message history to `.agent/sessions/<id>.json` |
| `AutoMemoryListener` | `turn.end`, `session.end` | Counts turns; at session end, extracts facts via JEV+LLM |
| `WebhookListener` | `*` (if `webhook_url` set) | Fire-and-forget POST of selected events |
| `PlanListener` | `plan.proposed` | Shows the plan panel; approve / edit-in-`$EDITOR` / cancel |

### `DisplayListener` highlights
- Streams text with `markup=False` so code isn't mis-parsed as Rich markup.
- Renders `todo_write` as a task panel (`Tasks  2/5 done`).
- Shows `remember`/`forget` as `💾 …`.
- Renders file edits as success lines, `read/grep/find` collapsed to a preview panel.
- Tool icons: `⬡` git, `$` bash, `🌐` web, `⚙` default.

### `ApprovalListener` — permission modes
Read from `cfg.approval_mode` (see `configuration.md`):

| Mode | Reads | Writes | Bash |
|---|---|---|---|
| `default` | auto | prompt | prompt |
| `acceptEdits` | auto | auto | prompt |
| `auto` | auto | auto | JEV decides; prompt only if judged risky |
| `bypassPermissions` | auto | auto | auto |

- `allowed_tools` / `disallowed_tools` in config override everything
  (`disallowed` sets `cancelled=True`).
- Read-only tools and `mcp__git__git_log` / `git_diff` are always auto-approved.
- Prompt choices: `[y/N/a/q]` — `a` = "approve all writes this turn", `q` = cancel.
- In `auto` mode bash safety comes from `routing.should_approve_bash` (JEV), with a
  dangerous-pattern fallback.

> **Gotcha — `auto` mode blocks the loop briefly.** It calls
> `asyncio.get_event_loop().run_until_complete(...)` inside a *sync* handler. It's
> time-boxed to ~6s and falls back to pattern matching. Be careful modifying this.

### `TranscriptListener` / `WebhookListener`
Both subscribe to the wildcard and serialise events with a `_serialisable` helper
that stringifies anything non-JSON. Transcript failures are swallowed; webhook
delivery uses `asyncio.create_task` (never blocks the turn).

---

## 6. Registering a listener

All registration happens in `repl.run_repl`:

```python
DisplayListener().register(bus)
ApprovalListener(cfg).register(bus)
CostTrackerListener().register(bus)
TranscriptListener(session_id, cwd).register(bus)
if cfg.plan_mode:
    PlanListener().register(bus)
WebhookListener(cfg).register(bus)
# ...after messages list exists:
PersistenceListener(session_id, messages, cwd).register(bus)
AutoMemoryListener(cfg, cwd, messages).register(bus)
```

A minimal listener:

```python
class MyListener:
    def register(self, bus):
        from agent.events.types import TOOL_AFTER
        bus.on(TOOL_AFTER, self._on_tool_after)

    def _on_tool_after(self, event):
        print(f"{event.data.tool} took {event.data.duration_ms}ms")
```

> **Rule — register in `repl.py`, not `loop.py`.** The loop must stay listener-free.

> **Gotcha — persistence & auto-memory need the `messages` list.** They're registered
> *after* the history is loaded/built, because they hold a reference to it. Order
> matters in `run_repl`.

---

## 7. Key files

- `src/agent/events/bus.py` — `Event`, `EventBus`
- `src/agent/events/types.py` — all event constants
- `src/agent/events/payloads.py` — all Pydantic payload models (`ToolBeforePayload`, `ErrorPayload`, etc.)
- `src/agent/events/listeners/*.py` — the eight built-in listeners

## See also

- [`architecture.md`](architecture.md) §3–§4 — where events fire in a turn
- [`tool-system.md`](tool-system.md) §4 — `tool.before`/`tool.after` around dispatch
- [`memory-and-skills.md`](memory-and-skills.md) — AutoMemoryListener details