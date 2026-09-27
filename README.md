# my-coding-agent

A coding agent that runs in your terminal — read, write, and edit files, run shell
commands, search the web, and manage git, driven by natural language. Built from
scratch in Python on top of any OpenAI-compatible provider (default: OpenRouter).

> **Contributors / AI agents:** start with [`AGENT.md`](AGENT.md) and the deep-dive
> docs in [`docs/agent_docs/`](docs/agent_docs/README.md). This README is the
> human-facing quick start.

---

## Features

- **Interactive REPL** and **single-shot** (`-p "…"`) modes
- **Native tools**: `read_file`, `write_file`, `edit_file`, `grep_files`, `find_files`, `bash`
- **Tool groups** (`core` / `web` / `git` / `mcp`) so the model only sees what it needs
- **Plan mode** — the agent proposes a numbered plan you approve or edit before it acts
- **Event-driven** — display, approval, cost tracking, persistence, and memory are all pluggable listeners
- **MCP client** — connect git, GitHub, and any other MCP server (stdio or HTTP)
- **Plugins** — add tools by dropping a file in `agent_tools/`
- **Skills & slash commands** — reusable prompts in `.agent/skills/` / `.agent/commands/`
- **Auto-memory** — the agent remembers project facts across sessions
- **Context compaction** — long conversations are summarised automatically
- **Session persistence** — save and resume conversations
- **Subagents** — spawn parallel sub-agents for divisible tasks
- **Web search & fetch** — optional, via `--web`

---

## Requirements

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) for dependency management
- An API key for an OpenAI-compatible provider (default: [OpenRouter](https://openrouter.ai))

---

## Quick start

```bash
# 1. Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Provide your API key
echo "OPENROUTER_API_KEY=sk-or-..." > .env

# 4. Run
uv run agent                 # interactive REPL
uv run agent -p "explain src/agent/loop.py"   # single-shot
```

---

## CLI reference

```
Usage: agent [OPTIONS]

Options:
  -p, --prompt TEXT        Run a single prompt and exit (non-interactive)
  -r, --resume TEXT        Resume a previous session by ID
  -m, --model TEXT         Model to use (overrides settings)
      --system-file PATH   Path to a custom system prompt file
      --no-approval        Auto-approve all tool calls (bypassPermissions)
      --accept-edits       Auto-approve edits; still prompt for bash
      --web                Enable web search and fetch tools
      --no-plan            Disable plan mode
      --no-memory          Disable end-of-session memory extraction
  -l, --list-sessions      List recent sessions and exit
      --webhook-url URL    POST events to this URL
      --webhook-events ... Comma-separated event types to forward
  -V, --version            Show version and exit
      --help               Show help and exit
```

### Slash commands (interactive)

| Command | What it does |
|---|---|
| `/help` | Show commands |
| `/tools` | List active tools / groups (`/tools on git`, `/tools off web`) |
| `/plan [task]` | Show the TODO list, or ask the agent to plan a task |
| `/compact` | Summarise the conversation to free context |
| `/sessions` | List recent sessions |
| `/memory` | View / delete remembered facts |
| `/mcp` | List MCP servers (`/mcp add <name> <cmd>`) |
| `/skills` | List available skills |
| `/init` | Generate an `AGENT.md` for this project |
| `/clear` | Reset conversation history |
| `/exit` | End the session |
| `/<skill-name>` | Run a skill from `.agent/skills/` |

---

## Configuration

Settings merge in this order (highest wins):

1. CLI flags
2. `.agent/settings.local.json` (personal, gitignored)
3. `.agent/settings.json` (project, committed)

Example `.agent/settings.json`:

```json
{
  "model": "deepseek/deepseek-v4.1-flash",
  "approvalMode": "acceptEdits",
  "planMode": true,
  "mcpServers": {
    "git": { "command": ["uvx", "mcp-server-git", "--repository", "."] }
  }
}
```

See [`docs/agent_docs/configuration.md`](docs/agent_docs/configuration.md) for every
option, and [`docs/agent_docs/mcp-integration.md`](docs/agent_docs/mcp-integration.md)
for MCP setup.

---

## Development

```bash
uv run pytest           # run tests
uv run ruff check src   # lint
uv run mypy src         # type check
```

Project layout:

```
src/agent/
  main.py            CLI entry point (typer)
  repl.py            interactive loop, slash commands, listener wiring
  loop.py            the agent loop (streams the LLM, dispatches tools)
  routing.py         JEV decisions (tool routing, approval, memory intent)
  context.py         conversation compaction
  subagent.py        parallel sub-agents
  tools/             native tools + registry + plugin loader
  events/            event bus, event types, listeners
  mcp/               MCP client + manager
  config/            Config + settings merge
  memory/            system prompt, auto-memory, skills
tests/               pytest suite
agent_tools/         example plugin tools
docs/agent_docs/     deep-dive documentation
```

---

## Documentation map

| Doc | For |
|---|---|
| [`AGENT.md`](AGENT.md) | Agents/contributors: the map + hard rules (loaded every session) |
| [`docs/agent_docs/README.md`](docs/agent_docs/README.md) | Index of all deep-dive docs |
| [`docs/agent_docs/architecture.md`](docs/agent_docs/architecture.md) | How the system fits together |
| [`docs/agent_docs/tool-system.md`](docs/agent_docs/tool-system.md) | Tools, groups, dispatch |
| [`docs/agent_docs/events-and-listeners.md`](docs/agent_docs/events-and-listeners.md) | Event bus + listeners |
| [`docs/agent_docs/mcp-integration.md`](docs/agent_docs/mcp-integration.md) | MCP servers |
| [`docs/agent_docs/configuration.md`](docs/agent_docs/configuration.md) | All config options |
| [`docs/agent_docs/memory-and-skills.md`](docs/agent_docs/memory-and-skills.md) | Memory, skills, system prompt |
| [`docs/agent_docs/testing.md`](docs/agent_docs/testing.md) | Running and writing tests |
| [`docs/agent_docs/adding-a-tool.md`](docs/agent_docs/adding-a-tool.md) | Recipes for extending the agent |
| [`docs/agent_docs/glossary.md`](docs/agent_docs/glossary.md) | Terms and acronyms |
| [`docs/PLAN.md`](docs/PLAN.md) | Original design doc (historical) |

---

## Security notes

- Tool output is wrapped in `<tool_result>` tags and treated as untrusted data.
- File tools reject paths that escape the working directory.
- Destructive bash commands are gated by the approval listener (per `approvalMode`).
- **Plugins and skills execute code in-process** with no sandbox — only use trusted ones.
