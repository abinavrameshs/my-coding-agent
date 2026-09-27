# Agent documentation index

This directory (`docs/agent_docs/`) holds the **deep-dive documentation** for the
`my-coding-agent` codebase. It is written so that an AI coding agent (or a new human
contributor) can understand and safely modify the repo **by reading Markdown only**,
without having to reverse-engineer the source first.

## How this documentation is organised

The root **`AGENT.md`** is the entry point. It is injected into every agent session
and stays deliberately short (progressive disclosure). Everything with detail lives
here, in files you read *on demand* when the task calls for it.

```
AGENT.md                          ← always loaded; map + hard rules
docs/agent_docs/                  ← read only when relevant (this folder)
  README.md                       ← you are here
  architecture.md                 ← data flow, layers, module boundaries
  tool-system.md                  ← tool registry, groups, schema filtering, dispatch
  events-and-listeners.md         ← event catalog, bus mutation contract, listeners
  mcp-integration.md              ← MCP client/manager, transports, namespacing
  configuration.md                ← settings hierarchy, every Config field, JEV knobs
  memory-and-skills.md            ← system prompt, auto-memory, skills, scoped rules
  testing.md                      ← running and adding tests
  adding-a-tool.md                ← step-by-step recipes for all 4 extension points
  glossary.md                     ← project-specific terms
```

## Reading guide — "I want to…"

| Goal | Start with | Then |
|---|---|---|
| Understand the whole system | `architecture.md` | `events-and-listeners.md`, `tool-system.md` |
| Trace what happens on a user turn | `architecture.md` § "A turn, end to end" | — |
| Understand the LLM streaming loop | `architecture.md` § "The agent loop" | — |
| Know how tools are chosen & run | `tool-system.md` | `adding-a-tool.md` |
| React to / add lifecycle hooks | `events-and-listeners.md` | — |
| Wire up git or another MCP server | `mcp-integration.md` | `configuration.md` |
| Change behaviour/config | `configuration.md` | — |
| Work on memory or slash commands | `memory-and-skills.md` | — |
| Run or write tests | `testing.md` | — |
| Add a tool / plugin / skill | `adding-a-tool.md` | `tool-system.md` |
| Decode an unfamiliar term | `glossary.md` | — |

## The 30-second mental model

```
 main.py  (CLI flags) ──► config/config.py (Config)
       │
       ▼
   repl.py  ── creates EventBus, registers listeners, starts MCP servers,
       │        builds the tool registry, assembles the system prompt
       │
       │  for each user input:
       ▼
  routing.py  ── JEV decides which tool groups to enable (runs in parallel)
       │
       ▼
    loop.py  ── stream LLM → emit events → execute tool calls (parallel)
       │            ▲                              │
       │            └──── recurse until no tools ──┘
       ▼
  events/bus.py  ── delivers every event to every listener
       │
       ▼
  events/listeners/*  ── display · approval · cost · transcript · persistence · memory · webhook
```

**One sentence:** `repl.py` wires everything up, `loop.py` is the only place the LLM
is called, `routing.py` makes cheap "JEV" decisions, `registry.py` decides which tools
the model sees and runs them, and `events/` is how everything else observes the run.

## Documentation conventions

- Code references use `path/to/file.py` and, where useful, `file.py:line`.
- "**Rule**" callouts state invariants the codebase relies on — don't break them.
- "**Gotcha**" callouts flag non-obvious behaviour that has bitten people.
- Every deep-dive ends with a **"Key files"** list and a **"See also"** list.

## Keeping these docs current

Docs rot. When you change code, update the matching doc in the same change:

| If you change… | Update… |
|---|---|
| A module's public role or imports | `architecture.md` |
| Tool schemas, groups, or dispatch | `tool-system.md`, `adding-a-tool.md` |
| Event types or listener behaviour | `events-and-listeners.md` |
| MCP config format or routing | `mcp-integration.md` |
| Any `Config` field or default | `configuration.md` |
| System prompt, skills, memory paths | `memory-and-skills.md` |
| Test layout or commands | `testing.md` |

---

See also: [`../../AGENT.md`](../../AGENT.md) (root onboarding).