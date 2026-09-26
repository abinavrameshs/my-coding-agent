"""Tests for system prompt assembly and prompt caching."""

import json
from pathlib import Path

import pytest

from agent.config.config import Config
from agent.memory.loader import assemble_system_prompt, system_prompt_text


@pytest.fixture
def cfg() -> Config:
    return Config()


class TestAssembleSystemPrompt:
    def test_returns_list_of_blocks(self, tmp_path: Path, cfg: Config) -> None:
        blocks = assemble_system_prompt(tmp_path, cfg)
        assert isinstance(blocks, list)
        assert all(isinstance(b, dict) for b in blocks)
        assert all("type" in b and "text" in b for b in blocks)

    def test_base_instructions_always_present(self, tmp_path: Path, cfg: Config) -> None:
        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "coding agent" in combined.lower()

    def test_exactly_one_cache_control_on_last_stable_block(
        self, tmp_path: Path, cfg: Config
    ) -> None:
        blocks = assemble_system_prompt(tmp_path, cfg)
        cached = [b for b in blocks if "cache_control" in b]
        assert len(cached) == 1
        assert cached[0]["cache_control"] == {"type": "ephemeral"}

    def test_agent_md_loaded_when_present(self, tmp_path: Path, cfg: Config) -> None:
        (tmp_path / "AGENT.md").write_text("# Custom instructions\nAlways use httpx.")
        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "httpx" in combined

    def test_claude_md_loaded_as_fallback(self, tmp_path: Path, cfg: Config) -> None:
        (tmp_path / "CLAUDE.md").write_text("Use ruff for linting.")
        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "ruff" in combined

    def test_agent_md_takes_priority_over_claude_md(
        self, tmp_path: Path, cfg: Config
    ) -> None:
        (tmp_path / "AGENT.md").write_text("AGENT file")
        (tmp_path / "CLAUDE.md").write_text("CLAUDE file")
        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "AGENT file" in combined
        assert "CLAUDE file" not in combined

    def test_scoped_rule_with_matching_glob_included(
        self, tmp_path: Path, cfg: Config
    ) -> None:
        rules_dir = tmp_path / ".agent" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text(
            "---\nglobs:\n  - '*.py'\n---\nAlways add type hints."
        )
        (tmp_path / "main.py").write_text("def foo(): pass")

        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "type hints" in combined

    def test_scoped_rule_with_non_matching_glob_excluded(
        self, tmp_path: Path, cfg: Config
    ) -> None:
        rules_dir = tmp_path / ".agent" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "go.md").write_text(
            "---\nglobs:\n  - '*.go'\n---\nUse gofmt."
        )
        # No .go files in tmp_path

        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "gofmt" not in combined

    def test_rule_without_globs_always_included(
        self, tmp_path: Path, cfg: Config
    ) -> None:
        rules_dir = tmp_path / ".agent" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "general.md").write_text("Never commit secrets.")

        blocks = assemble_system_prompt(tmp_path, cfg)
        combined = " ".join(b["text"] for b in blocks)
        assert "Never commit secrets" in combined

    def test_memory_appended_without_cache_control(
        self, tmp_path: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hashlib
        project_hash = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
        mem_dir = tmp_path / "home" / ".agent" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / f"{project_hash}.json").write_text(
            json.dumps({"http_library": "always use httpx"})
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

        blocks = assemble_system_prompt(tmp_path, cfg)
        mem_blocks = [b for b in blocks if "Remembered context" in b["text"]]
        assert len(mem_blocks) == 1
        assert "cache_control" not in mem_blocks[0]
        assert "httpx" in mem_blocks[0]["text"]

    def test_no_memory_file_no_memory_block(
        self, tmp_path: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        blocks = assemble_system_prompt(tmp_path, cfg)
        assert not any("Remembered context" in b["text"] for b in blocks)

    def test_deterministic_across_calls(self, tmp_path: Path, cfg: Config) -> None:
        (tmp_path / "AGENT.md").write_text("Be concise.")
        a = assemble_system_prompt(tmp_path, cfg)
        b = assemble_system_prompt(tmp_path, cfg)
        assert [x["text"] for x in a] == [x["text"] for x in b]


class TestSystemPromptText:
    def test_returns_string(self, tmp_path: Path, cfg: Config) -> None:
        text = system_prompt_text(tmp_path, cfg)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_all_block_texts(self, tmp_path: Path, cfg: Config) -> None:
        (tmp_path / "AGENT.md").write_text("Custom project rule here.")
        text = system_prompt_text(tmp_path, cfg)
        assert "Custom project rule here." in text
