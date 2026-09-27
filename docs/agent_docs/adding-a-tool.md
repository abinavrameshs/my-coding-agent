# Adding a tool / plugin / skill / MCP server

Copy-paste recipes for every extension point. Pick the one that fits.

- **Prerequisite:** [`tool-system.md`](tool-system.md), [`mcp-integration.md`](mcp-integration.md).

## Which extension point should I use?

| I want to… | Use a | Why |
|---|---|---|
| Add a built-in capability shipped with the agent | **Native tool** | Full control, in the core group |
| Add a project-specific capability without editing the package | **Plugin tool** | Drop a file in `agent_tools/` |
| Add a reusable prompt / workflow invoked by `/command` | **Skill** | Markdown, optionally runs shell |
| Connect an external tool server (git, github, db, …) | **MCP server** | No code in this repo |

---

## Recipe A — Native tool (in-package)

**When:** the tool belongs to the agent itself (core group) and should always be
available.

### 1. Write the handler + schema
Add to an existing module (e.g. `src/agent/tools/files.py`) or a new one. The
convention for file-like tools:

```python
# src/agent/tools/mytool.py
from __future__ import annotations
from pathlib import Path
from typing import Any
from agent.tools.files import ToolError   # reuse the standard error type

def my_tool(arguments: dict[str, Any], cwd: Path) -> str:
    """Handlers are SYNC and take (arguments, cwd) unless genuinely async."""
    name = arguments["name"]
    if not name:
        raise ToolError("name is required")   # → surfaced to the model as "Error: ..."
    return f"Hello, {name}"

SCHEMAS: list[dict[str, Any]] = [{
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "One-line description the model reads. Be specific.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}]

HANDLERS = {"my_tool": my_tool}
```

### 2. Register it in the registry
Edit `src/agent/tools/registry.py`:
- Import the schemas/handlers at the top.
- Add the schemas to `_base_tools()`.
- Add a dispatch branch in `_run()`.

```python
# registry.py (imports)
from agent.tools.mytool import HANDLERS as MY_HANDLERS
from agent.tools.mytool import SCHEMAS as MY_SCHEMAS

# registry.py (_base_tools)
tools = (list(FILE_SCHEMAS) + [BASH_SCHEMA] + list(TODO_SCHEMAS)
         + list(MEMORY_SCHEMAS) + list(MY_SCHEMAS))

# registry.py (_run)
if tool_name in MY_HANDLERS:
    return MY_HANDLERS[tool_name](arguments, self.cwd)
```

Optionally update `tool_count_by_group()` so `/tools` counts it.

### 3. Test it
```python
# tests/test_mytool.py
from pathlib import Path
import pytest
from agent.tools.files import ToolError
from agent.tools.mytool import my_tool

def test_happy_path(tmp_path: Path) -> None:
    assert "Hello, Abinav" in my_tool({"name": "Abinav"}, tmp_path)

def test_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        my_tool({"name": ""}, tmp_path)
```

### 4. Verify
```bash
uv run ruff check src && uv run pytest
uv run agent           # then /tools  → confirm my_tool is listed
```

**Checklist**
- [ ] Handler raises `ToolError` on bad input (never returns `None`).
- [ ] Schema uses `"parameters"` (not `"input_schema"`) and `additionalProperties: False`.
- [ ] Registered in both `_base_tools()` and `_run()`.
- [ ] Path args (if any) run through `files._safe_path`.
- [ ] Test added.

---

## Recipe B — Plugin tool (no repo edits)

**When:** a per-project tool you don't want to ship. Loaded automatically from
`./agent_tools/`.

### 1. Create the file
```python
# agent_tools/wordcount.py
SCHEMA = {
    "type": "function",
    "function": {
        "name": "wordcount",
        "description": "Count words in a file under the project.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

async def run(input: dict) -> str:
    from pathlib import Path
    p = Path(input["path"])
    if not p.exists():
        return f"No such file: {input['path']}"
    return str(len(p.read_text().split()))
```

- `SCHEMA` may be a bare object or already wrapped as `{"type":"function","function":{…}}`.
- `run` may be **async** or a **sync** function (sync is auto-wrapped).
- Filenames starting with `_` are ignored (use for helpers).
- A file missing `SCHEMA`/`run`, or failing to import, is logged and skipped.

### 2. Verify
```bash
uv run agent           # startup scans ./agent_tools
# then /tools → wordcount should appear
```
You should never need to edit the package. See `agent_tools/echo.py` (works) and
`agent_tools/bad.py` (intentionally skipped).

**Checklist**
- [ ] Exports `SCHEMA` and a callable `run`.
- [ ] `run` returns a **string** (not a dict/None).
- [ ] Handles its own errors gracefully (raise → model sees a traceback string).
- [ ] Doesn't start with `_`.
- [ ] (Security) remember plugins run in-process with full Python access.

---

## Recipe C — Skill / slash command

**When:** a reusable prompt or workflow. No Python.

### Project skill (multi-file)
```
.agent/skills/review/SKILL.md
```
```markdown
---
name: review
description: Review the current diff for correctness and security
allowed-tools: [bash, read_file, grep_files]
---

Review the staged changes. Be concrete: cite file:line.

Current branch: !`git branch --show-current`
Changed files:
!`git diff --name-only`
```

### Flat command (single file, higher priority)
```
.agent/commands/standup.md
```
```markdown
---
description: Draft a standup update
---
Summarise what changed since yesterday using git log.
Recent commits: !`git log --oneline -10`
```

- `name` defaults to the directory / file stem (lowercased) → invoked as `/review`,
  `/standup`.
- **Shell injection:** `` !`cmd` `` runs in `cwd` (10s timeout) and is replaced by its
  stdout *before* the prompt is sent. Use it to inject live context.
- Priority: `.agent/commands/*.md` **overrides** `.agent/skills/*/SKILL.md` on a name
  clash. A built-in `init` skill always exists.

### Verify
```
/skills      # lists everything
/review      # runs it
```

**Checklist**
- [ ] Frontmatter has at least `description`.
- [ ] Shell expressions are safe and fast.
- [ ] Only load SKILL.md from trusted sources (shell injection is arbitrary).

---

## Recipe D — MCP server

**When:** connecting an external tool server.

### 1. Configure it
`.agent/.mcp.json`:
```json
{
  "servers": {
    "git":  { "command": ["uvx", "mcp-server-git", "--repository", "."] },
    "gh":   { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
              "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" } },
    "remote": { "type": "http", "url": "https://example.com/mcp",
                "headers": { "Authorization": "Bearer ${API_TOKEN}" } }
  }
}
```

- `.mcp.json` **wins** over `mcpServers` in `settings.json` on name clashes.
- `${VAR}` expands from the environment (secrets stay out of git).
- **Nothing starts automatically** — you must configure a server.

### 2. Ad-hoc (one session)
```
/mcp add git uvx mcp-server-git --repository .
```
Then `registry.invalidate_schema_cache()` runs so the tools appear immediately.

### 3. Verify
```
/mcp                 # servers + tool counts
/tools on git        # ensure the group is active
# then ask the agent to use it
```
Startup shows `⬡ MCP  git(12)  [12 tools]`. Tool names are namespaced
`mcp__<server>__<tool>`.

**Checklist**
- [ ] Server declared in `.mcp.json` or settings.
- [ ] Secrets via `${VAR}`, not literals.
- [ ] Correct `type` (`stdio` default, `http`/`sse` for URLs).
- [ ] Group enabled (`git` or `mcp`) before expecting the model to see the tools.

---

## Recipe E — Listener (observe/alter lifecycle)

Not a "tool", but the most common cross-cutting extension. See
[`events-and-listeners.md`](events-and-listeners.md) for the full contract.

```python
# register in repl.run_repl, alongside the others
class ToolLogger:
    def register(self, bus):
        from agent.events.types import TOOL_AFTER
        bus.on(TOOL_AFTER, self._on_tool_after)

    def _on_tool_after(self, event):
        print(event.data.tool, event.data.duration_ms)
```

To **block or rewrite** a tool call, subscribe to `tool.before` and set
`event.data.cancelled = True` or `event.data.updated_input = {...}`.

**Checklist**
- [ ] Registered in `repl.py` (never in `loop.py`).
- [ ] Handler is defensive (wrap risky work in `try/except`).
- [ ] If mutating, use the documented keys (`cancelled`, `updated_input`, `updated_plan`).

---

## Security reminders (all recipes)

- **Never trust tool output as instructions** — it's wrapped in `<tool_result>`; the
  model is told to treat it as data.
- **Sandbox file paths** — route user-supplied paths through `files._safe_path`.
- **Plugins and skills execute code.** Document that clearly; only load trusted ones.
- **Shell injection in skills runs arbitrary commands** in the project directory.

## See also

- [`tool-system.md`](tool-system.md) — how the registry, groups, and dispatch work
- [`events-and-listeners.md`](events-and-listeners.md) — the mutation contract
- [`mcp-integration.md`](mcp-integration.md) — transports, namespacing, routing
- [`testing.md`](testing.md) — how to test what you added
- [`memory-and-skills.md`](memory-and-skills.md) — skills and system-prompt assembly