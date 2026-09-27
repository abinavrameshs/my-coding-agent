# Configuration

Everything configurable, and how the layers merge. The single source of truth is
**`src/agent/config/config.py`**.

- **Prerequisite:** [`architecture.md`](architecture.md).
- **Related:** [`mcp-integration.md`](mcp-integration.md) (MCP server config),
  [`memory-and-skills.md`](memory-and-skills.md) (`.agent/` layout).

---

## 1. The 3-level hierarchy

Configuration merges, **highest wins**:

| Level | Source | Scope | Committed? |
|---|---|---|---|
| 1 (highest) | CLI flags | this invocation | n/a |
| 2 | `.agent/settings.local.json` | this project, this user | no (gitignore) |
| 3 | `.agent/settings.json` | this project, all users | yes |

Plus, before all of these, **hardcoded defaults** in the `Config` model, and
**secrets** from `.env` (via `python-dotenv`, loaded at import time).

> **Note — the old PLAN.md described a 4-level hierarchy including
> `~/.agent/settings.json`.** The current code reads **only** the two project files
> above plus CLI overrides. There is no user-level settings file.

```python
def load_config(cwd=None, cli_overrides=None) -> Config:
    sources = [cwd/".agent"/"settings.json", cwd/".agent"/"settings.local.json"]
    merged = {}
    for path in sources:
        raw = _load_json(path)                     # {} if missing/invalid
        # "servers" key is normalised to "mcpServers"
        merged = deep_merge(merged, raw)
    if cli_overrides:
        merged = deep_merge(merged, {k: v for k, v in cli_overrides.items() if v is not None})
    return Config.model_validate(merged)
```

### Merge semantics
- `deep_merge(base, override)` merges recursively; **override wins on scalar
  conflicts**; nested dicts are merged key-by-key.
- CLI overrides skip `None` values (a flag not passed = no override).
- The key `servers` is renamed to `mcpServers` so `Config.mcp_servers` always wins.

### Key casing
`Config` uses `alias_generator=to_camel` **with** `populate_by_name=True`, so JSON
settings use **camelCase** (`baseUrl`, `planMode`, `mcpServers`) while Python/CLI use
**snake_case** (`base_url`, `plan_mode`). Both validate.

---

## 2. Secrets and environment

Read at import time in `config/config.py`:

| Env var | Default | Used for |
|---|---|---|
| `OPENROUTER_API_KEY` | `""` | API key for the LLM client. **Required** — `main.py` exits if empty |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Default `Config.base_url` |

`.env` is auto-loaded (`load_dotenv()` at module top). Typical `.env`:

```
OPENROUTER_API_KEY=sk-or-...
```

> **Gotcha — the key is read once at import.** If you set `OPENROUTER_API_KEY` after
> importing `config.config`, it won't be picked up. Set it in `.env` or the shell
> before launch.

---

## 3. Every `Config` field

Defaults as defined in the model. JSON key = camelCase.

| Field (snake) | JSON key | Type | Default | Meaning |
|---|---|---|---|---|
| `model` | `model` | str | `deepseek/deepseek-v4.1-flash` | Model id sent to the provider |
| `base_url` | `baseUrl` | str | `$OPENROUTER_BASE_URL` | OpenAI-compatible endpoint |
| `approval_mode` | `approvalMode` | str | `default` | `default` \| `acceptEdits` \| `auto` \| `bypassPermissions` |
| `plan_mode` | `planMode` | bool | `True` | Enable the `<plan>` approval gate |
| `web_search` | `webSearch` | bool | `False` | Activate the `WEB` tool group at startup |
| `auto_memory` | `autoMemory` | bool | `True` | Extract facts at session end |
| `parallel_planning` | `parallelPlanning` | bool | `True` | Allow `spawn_parallel` to run independent plan items concurrently |
| `max_parallel_subagents` | `maxParallelSubagents` | int | `4` | Max concurrent subagents per batch (width cap) |
| `max_tool_output_chars` | `maxToolOutputChars` | int | `10_000` | Bash output cap |
| `max_retries` | `maxRetries` | int | `3` | LLM retry attempts on transient errors |
| `context_limit` | `contextLimit` | int | `1_000_000` | Token budget; compaction fires at 80% |
| `system_file` | `systemFile` | str \| None | `None` | Reserved custom system prompt path |
| `webhook_url` | `webhookUrl` | str \| None | `None` | POST events here if set |
| `webhook_events` | `webhookEvents` | list[str] | `[]` | Event types to forward (empty = all) |
| `mcp_servers` | `mcpServers` | dict | `{}` | MCP servers by name (see MCP doc) |
| `allowed_tools` | `allowedTools` | list[str] | `[]` | Always auto-approve these tools |
| `disallowed_tools` | `disallowedTools` | list[str] | `[]` | Always block these tools |
| `jev_model` | `jevModel` | str | `~typesafe/jev-latest` | Decisions model |
| `jev_endpoint` | `jevEndpoint` | str | `https://openrouter.ai/api/alpha/decisions` | Decisions endpoint |
| `jev_threshold` | `jevThreshold` | float | `0.6` | Probability above which a `noul` answer is `True` |
| `jev_timeout` | `jevTimeout` | float | `8.0` | Seconds before a JEV call is abandoned |

---

## 4. Example `.agent/settings.json`

```json
{
  "model": "deepseek/deepseek-v4.1-flash",
  "baseUrl": "https://openrouter.ai/api/v1",
  "approvalMode": "acceptEdits",
  "planMode": true,
  "webSearch": false,
  "autoMemory": true,
  "mcpServers": {
    "git": { "command": ["uvx", "mcp-server-git", "--repository", "."] }
  },
  "disallowedTools": ["bash"]
}
```

Put per-user overrides in `.agent/settings.local.json` (and gitignore it).

---

## 5. CLI flags → overrides

`main.py` maps flags to snake_case override keys (skipping unset ones):

| Flag | Override key | Value |
|---|---|---|
| `--model` / `-m` | `model` | the model string |
| `--system-file` | `system_file` | path |
| `--web` | `web_search` | `True` |
| `--no-plan` | `plan_mode` | `False` |
| `--no-memory` | `auto_memory` | `False` |
| `--no-approval` | `approval_mode` | `"bypassPermissions"` |
| `--accept-edits` | `approval_mode` | `"acceptEdits"` |
| `--webhook-url` | `webhook_url` | URL |
| `--webhook-events` | `webhook_events` | comma-split list |

Other flags handled in `main.py` directly (not config): `--prompt/-p`, `--resume/-r`,
`--list-sessions/-l`, `--version/-V`.

> **Gotcha — `--no-approval` and `--accept-edits` both write `approval_mode`.**
> `--no-approval` is checked first, so it wins if both are passed.

---

## 6. The JEV decisions model

"JEV" is a lightweight decisions endpoint used for four cheap, boolean-ish calls
without a full text generation. All live in `src/agent/routing.py` and **all
fail-soft** (return a safe default on any error).

| Function | Question | Used by | Fallback |
|---|---|---|---|
| `auto_enable_groups` | which tool groups does this request need? | `repl.handle_turn` | do nothing |
| `should_approve_bash` | is this shell command safe to auto-run? | `ApprovalListener` (`auto` mode) | treat as risky → prompt |
| `should_extract_memory` | is this session worth remembering? | `AutoMemoryListener` | `turn_count >= 3` |
| `has_memory_intent` | does the user want a fact persisted? | `repl.handle_turn` | `False` |

### How a JEV call works
`JEVClient.decide(state, questions)` POSTs `{model, state, questions}` to
`cfg.jev_endpoint` and reads `answers`. Each question is `{"type": "noul", ...}`;
the answer's `noul` value is a probability, compared to `cfg.jev_threshold`:

```python
def _noul(self, answers, key) -> bool:
    return float(answers.get(key, {}).get("noul", 0)) >= self._cfg.jev_threshold
```

> **Gotcha — JEV needs an API key too.** `routing.py` reads `OPENROUTER_API_KEY`
> from `config.config` at call time. No key ⇒ every JEV call silently falls back.
> The agent still works (core tools + default approval), just without auto-routing.

> **Rule — never let JEV block a turn.** Every caller time-boxes with
> `asyncio.wait_for(..., timeout=cfg.jev_timeout)` and catches exceptions.

---

## 7. Where config is consumed

| Consumer | Fields |
|---|---|
| `loop.make_client` / `run_turn` | `base_url`, `model`, `max_retries` |
| `ToolRegistry` | `web_search`, `max_tool_output_chars`, `mcp_servers` |
| `ApprovalListener` | `approval_mode`, `allowed_tools`, `disallowed_tools` |
| `repl.handle_turn` | `context_limit`, `jev_timeout` |
| `AutoMemoryListener` | `auto_memory`, `jev_timeout` |
| `WebhookListener` | `webhook_url`, `webhook_events` |
| `memory.loader` | `plan_mode` (injects plan instructions) |
| `subagent.run_parallel` | `max_parallel_subagents`, `parallel_planning` |
| `MCPManager` | `mcp_servers` |

---

## 8. `.agent/` directory layout

Runtime state lives under the project's `.agent/` directory:

```
.agent/
├── settings.json          # project config (committed)
├── settings.local.json    # personal overrides (gitignored)
├── .mcp.json              # MCP server config (optional)
├── memory.json            # persistent project facts (committable)
├── rules/                 # scoped rules (globs frontmatter)
├── skills/<name>/SKILL.md # project skills
├── commands/<name>.md     # flat commands (highest-priority skills)
├── sessions/<id>.json     # saved conversations (gitignored)
└── transcripts/<id>.jsonl # event logs (gitignored)
```

---

## 9. Key files

- `src/agent/config/config.py` — `Config`, `MCPServerConfig`, `load_config`, `deep_merge`
- `src/agent/main.py` — CLI flags → overrides
- `src/agent/routing.py` — JEV usage

## See also

- [`mcp-integration.md`](mcp-integration.md) — `mcpServers` / `.mcp.json`
- [`memory-and-skills.md`](memory-and-skills.md) — memory, rules, skills files
- [`events-and-listeners.md`](events-and-listeners.md) — approval modes, webhook