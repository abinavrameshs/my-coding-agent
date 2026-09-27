# Testing

How to run the test suite and how to add tests that fit the existing patterns.

- **Config:** `pyproject.toml` → `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`,
  `testpaths = ["tests"]`).
- **Location:** `tests/`.

---

## 1. Running tests

```bash
uv run pytest                 # whole suite
uv run pytest tests/test_tools_files.py           # one file
uv run pytest tests/test_tools_files.py::TestReadFile  # one class
uv run pytest -k "path_escape"                    # by name substring
uv run pytest -q                                  # quiet
```

Related checks:

```bash
uv run ruff check src tests   # lint
uv run mypy src               # types
```

> **Note — `asyncio_mode = "auto"`.** You do **not** need `@pytest.mark.asyncio` on
> async tests (though some existing tests add it harmlessly). Just write
> `async def test_...`.

---

## 2. Test layout

```
tests/
├── __init__.py
├── test_event_bus.py            # EventBus: registration, wildcard, mutation, ordering
├── test_tools_files.py          # read/write/edit/grep/find + path-escape safety
├── test_tools_bash.py           # run_bash: output, stderr, truncation, timeout, warnings
├── test_settings.py             # Config merge hierarchy, camelCase, validation
├── test_memory_loader.py        # system prompt assembly, cache marker, rules, memory
├── test_skills.py               # skill discovery, frontmatter, shell injection
├── test_context.py              # compaction: no-op, shrink, preserve system, errors
├── test_subagent.py             # subagent depth, events, delegation (unit)
├── test_subagent_integration.py # _SubagentRegistry end-to-end: schemas_for, RegistryProtocol
├── test_parallel.py             # parallel.py: batching, cycle detection, file conflicts
├── test_routing_plan.py         # routing.py: classify_plan_items dependency logic
├── test_plugins.py              # plugin loader: valid/invalid/sync/async
└── test_placeholder.py          # trivial placeholder
```

> **Note — `test_settings.py` still names a "4-level" hierarchy** in comments and
> `test_all_four_levels_priority_order`, but the actual loader reads 3 sources
> (defaults → project files → CLI). The test patches `Path.home` but the loader no
> longer consults `~/.agent`. Treat `configuration.md` §1 as authoritative.

---

## 3. Testing philosophy (match the existing style)

The suite favours **fast, isolated, no-network** tests:

- **Pure functions** are tested directly (e.g. `deep_merge`, `_parse_frontmatter`,
  `run_bash`, file handlers).
- **Filesystem work** uses pytest's `tmp_path` fixture — never the real repo.
- **The LLM is never called.** Tests that touch the loop mock the client
  (`AsyncMock`) or patch `agent.loop.run_turn` / `agent.loop.make_client`.
- **Subagents & compaction** are tested by patching `agent.loop.*` and
  `agent.memory.loader.assemble_system_prompt`.
- Tests are grouped in `class TestXxx` with descriptive method names.

---

## 4. Patterns to copy

### Filesystem test (native tools)
```python
def test_reads_file_with_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("alpha\nbeta")
    out = read_file({"path": "f.txt"}, tmp_path)   # handler takes (args, cwd)
    assert "alpha" in out and "   1 |" in out
```

### Async test (loop / bus)
```python
async def test_listener_can_cancel_event() -> None:
    from agent.events.payloads import ToolBeforePayload
    bus = EventBus()
    bus.on(TOOL_BEFORE, lambda e: setattr(e.data, "cancelled", True))
    event = await bus.emit(Event(TOOL_BEFORE, ToolBeforePayload(tool="bash")))
    assert event.data.cancelled is True
```

### Mocking the LLM
```python
mock_resp = MagicMock()
mock_resp.choices = [MagicMock()]
mock_resp.choices[0].message.content = "Summary."
client = AsyncMock()
client.chat.completions.create = AsyncMock(return_value=mock_resp)
_, before, after = await compact_messages(messages, cfg, client)
```

### Patching the loop (subagents)
```python
with patch("agent.loop.run_turn", fake_run_turn), \
     patch("agent.loop.make_client"), \
     patch("agent.memory.loader.assemble_system_prompt",
           return_value=[{"type": "text", "text": "sys"}]):
    result = await run_subagent("task", None, bus, registry, cfg, depth=0, cwd=tmp_path)
```

### Config test (write settings, load, assert)
```python
def test_local_overrides_shared(tmp_path: Path) -> None:
    d = tmp_path / ".agent"; d.mkdir()
    (d / "settings.json").write_text(json.dumps({"model": "shared"}))
    (d / "settings.local.json").write_text(json.dumps({"model": "local"}))
    assert load_config(cwd=tmp_path).model == "local"
```

### Plugin test (write a plugin file, load it)
```python
_write_plugin(tmp_path / "agent_tools", "echo", """
    SCHEMA = {"type": "function", "function": {"name": "echo", ...}}
    async def echo_run(input): return input["text"]
""")
plugins = load_plugins(tmp_path)
assert "echo" in plugins
```

---

## 5. What to test when you add a feature

| You added/changed… | Test… |
|---|---|
| A native tool handler | Happy path + error path (raise `ToolError`) + path-escape rejection |
| A new `Config` field | A `load_config` test asserting the merge and default |
| A new event or listener | `EventBus` mutation/ordering behaviour, and the listener's handler |
| A new skill / rule behaviour | `discover_skills` / `assemble_system_prompt` output |
| A new MCP transport branch | Config parsing (`MCPServerConfig`) at minimum; avoid live servers |
| Loop behaviour | Patch `agent.loop.make_client` and assert on emitted events |

> **Rule — never hit the network in tests.** No live LLM, no live MCP server, no
> web fetch. Mock or patch at the boundary.

> **Rule — clean up global state.** Some modules hold module-level state
> (`todo._items`, `approval._approve_all`). Call the reset helper
> (`reset_todos()`) or use fresh instances so tests don't leak into each other.

---

## 6. Known gaps / opportunities

- No tests for `loop.run_turn` end-to-end (only subagents patch it).
- No tests for `routing.py` (JEV) — `test_routing_plan.py` covers `classify_plan_items`; the live JEV client still has no tests.
- No tests for `mcp/manager.py` routing or `MCPManager.call` name parsing.
- `test_placeholder.py` is a leftover; replace with real tests or remove.
- `test_settings.py` docstrings still reference the retired 4-level model.
- `test_subagent_integration.py` covers `_SubagentRegistry` end-to-end, including the `schemas_for` override that unit tests would miss.

---

## 7. Key files

- `tests/` — the suite
- `pyproject.toml` — pytest/ruff/mypy config
- `docs/agent_docs/adding-a-tool.md` — test recipe for new tools

## See also

- [`architecture.md`](architecture.md) — error-handling philosophy (what to assert)
- [`events-and-listeners.md`](events-and-listeners.md) — bus semantics under test
- [`configuration.md`](configuration.md) — the real settings hierarchy