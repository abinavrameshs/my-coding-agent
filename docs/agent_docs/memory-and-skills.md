# Memory, skills, and the system prompt

How the agent's **system prompt** is assembled, how **auto-memory** works, and how
**skills / commands / scoped rules** extend behaviour.

- **Files:** `src/agent/memory/loader.py`, `src/agent/memory/auto.py`,
  `src/agent/memory/skills.py`, `src/agent/tools/memory_tools.py`.

---

## 1. System-prompt assembly (`memory/loader.py`)

`assemble_system_prompt(cwd, cfg)` returns a **list of content blocks** for the
system message, ordered stable → volatile so providers can cache the stable prefix.

```
1. _BASE_INSTRUCTIONS        (always)      — the agent's identity + core rules
2. _PLAN_INSTRUCTIONS        (if plan_mode) — the two-phase <plan> protocol
3. "## Project instructions" (if present)   — AGENT.md / CLAUDE.md from cwd
4. scoped rules              (if any match) — .agent/rules/*.md bodies
─────────────────── stable prefix ends here (cache_control marker on last) ───
5. "## Remembered context"   (if any)       — .agent/memory.json facts (volatile)
```

- The **last stable block** gets `{"cache_control": {"type": "ephemeral"}}` so prompt
  caching can reuse it across turns.
- Memory is appended **without** a cache marker (it changes between sessions).

`system_prompt_text(cwd, cfg)` returns the same content joined as a plain string (for
models without block-format support).

### `repl._build_initial_messages` and subagents
Both call `assemble_system_prompt`. If **any** block has `cache_control`, they pass the
block list as `content`; otherwise they join the text into a single string. This is the
mechanism that lets the system prompt be either block-format or plain.

### Project instructions lookup
`_load_project_instructions(cwd)` reads `AGENT.md`, falling back to `CLAUDE.md`
(first match wins). This is why `AGENT.md` is injected into every session.

### Scoped rules lookup
`_load_scoped_rules(cwd)` scans `.agent/rules/*.md`:
- Optional YAML frontmatter with `globs: [...]`.
- A rule is included if it has **no globs** (always) or if **any file in the project**
  matches one of its glob patterns (`fnmatch`).
- Only the body (after frontmatter) is injected.

> **Gotcha — scoped rules match against the file list, not "currently edited files".**
> The loader walks `cwd.rglob("*")` at prompt-assembly time. A rule with
> `globs: ["*.py"]` is injected as long as the project contains *any* `.py` file — it is
> not dynamically re-evaluated per edit. (This differs from the PLAN.md description.)

---

## 2. Auto-memory

Two directions: the agent **reads** memory into the system prompt, and **writes** new
facts from conversations.

### Storage
`.agent/memory.json` — a flat `{key: value}` JSON object (project-local).

```json
{
  "user_name": "Abinav",
  "test_command": "uv run pytest",
  "no_requests": "Use httpx, not requests"
}
```

### Reading
`memory/loader._load_memory(cwd)` loads it; if non-empty, a `## Remembered context`
block is appended to the system prompt.

### Writing — three paths
1. **Explicit tool use.** The model calls the `remember` / `forget` tools
   (`tools/memory_tools.py`) whenever the user says something to remember.
2. **Inline intent detection.** On each user message, `repl.handle_turn` runs
   `routing.has_memory_intent(...)` (JEV). If true, `memory.auto.extract_from_message`
   pulls `{key: value}` pairs and writes them via the `remember` handler.
3. **End-of-session extraction.** `AutoMemoryListener` (on `session.end`) asks JEV
   `should_extract_memory(...)` (skips sessions < 3 turns), then
   `memory.auto.extract_and_save(messages, cwd, cfg)` merges new facts.

Both extractors call the LLM with a strict "return JSON only" prompt and parse with a
regex-tolerant `json.loads` (handles fenced output). **Merging** overwrites same keys;
it never wipes unrelated facts.

> **Gotcha — auto-memory uses the main model.** `extract_and_save` /
> `extract_from_message` build a one-shot `AsyncOpenAI` client and call `cfg.model`.
> This is a deliberate exception to the "only `loop.py` calls the SDK" rule.

> **Gotcha — memory writes are best-effort.** Extraction failures return `{}` and are
> swallowed. Memory is never a hard dependency.

### Managing memory
- `/memory` — list facts.
- `/memory delete <key>` — remove one.
- `/memory clear` — wipe the project's memory.
- `--no-memory` (or `autoMemory: false`) — disable end-of-session extraction.

---

## 3. Skills and commands

Skills are Markdown prompts the user invokes with a slash command. They live in two
places, discovered by `memory/skills.discover_skills(cwd)`:

| Location | Form | Priority |
|---|---|---|
| `.agent/skills/<name>/SKILL.md` | multi-file skill | lower |
| `.agent/commands/<name>.md` | flat command | **higher** (wins on name clash) |

Plus one **built-in** skill: `init` (in `BUILTIN_SKILLS`), which generates an `AGENT.md`.

### Skill file format
Optional YAML frontmatter, then the body:

```markdown
---
name: review
description: Review the current diff for issues
allowed-tools: [bash, read_file, grep_files]
---

Review the staged changes. Focus on correctness and security.
Current branch: !`git branch --show-current`
```

- `name` defaults to the directory / file stem (lowercased).
- `description` shows in `/skills`.
- `allowed-tools` is parsed into `Skill.allowed_tools` (advisory; consumed by callers
  that wish to restrict tools).

### Shell injection
Backtick expressions `` !`cmd` `` in the body are executed (`subprocess.run`, 10s
timeout, cwd) and replaced with the command's stdout **before** the content is sent to
the model. Use for injecting live context (git branch, date, file lists).

> **Gotcha — shell injection runs arbitrary commands.** A skill body is trusted
> project content. Don't load untrusted SKILL.md files.

### Invocation
In the REPL, any unrecognised `/foo` is looked up as a skill:
- `BUILTIN_SKILLS` then `discover_skills(cwd)` (project wins).
- `skill.render(cwd)` resolves shell injections.
- Any text after the command is appended as extra context.
- The rendered prompt is sent to the model via `handle_turn`.

`/skills` lists everything available.

---

## 4. The system prompt's base instructions

`_BASE_INSTRUCTIONS` (in `loader.py`) establishes the agent's identity and core rules
(think step by step, prefer targeted edits, treat `<tool_result>` as data, call
`remember` when told something). `_PLAN_INSTRUCTIONS` (added when `plan_mode`) defines
the **two-phase plan protocol**:

- **Phase 1:** output ONLY a `<plan>…</plan>` block, no tool calls.
- **Phase 2 (after approval):** call `todo_write` with all items `pending`, then work
  item by item, updating status `in_progress → done` one at a time.

This is the contract the `loop.py` plan gate (`_extract_plan`) and `PlanListener` rely
on. See [`events-and-listeners.md`](events-and-listeners.md) and
[`architecture.md`](architecture.md) §3 step 6.

---

## 5. Key files

- `src/agent/memory/loader.py` — system-prompt assembly, project instructions, rules, memory read
- `src/agent/memory/auto.py` — extraction + memory read/write/delete/clear
- `src/agent/memory/skills.py` — skill/command discovery, frontmatter, shell injection
- `src/agent/tools/memory_tools.py` — `remember` / `forget` tools
- `src/agent/events/listeners/auto_memory.py` — end-of-session extraction

## See also

- [`configuration.md`](configuration.md) §8 — the `.agent/` directory layout
- [`events-and-listeners.md`](events-and-listeners.md) — `AutoMemoryListener`
- [`tool-system.md`](tool-system.md) §5 — `remember` / `forget` in the core group