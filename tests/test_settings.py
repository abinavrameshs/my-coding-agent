"""Tests for the 4-level config hierarchy."""

import json
from pathlib import Path

import pytest

from agent.config.config import Config, deep_merge, load_config


class TestDeepMerge:
    def test_override_wins_on_scalar(self) -> None:
        result = deep_merge({"a": 1}, {"a": 2})
        assert result["a"] == 2

    def test_base_keys_preserved(self) -> None:
        result = deep_merge({"a": 1, "b": 2}, {"a": 99})
        assert result["b"] == 2

    def test_nested_dicts_merged_recursively(self) -> None:
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 99, "z": 3}}
        result = deep_merge(base, override)
        assert result["a"] == {"x": 1, "y": 99, "z": 3}

    def test_override_replaces_scalar_with_scalar(self) -> None:
        result = deep_merge({"a": "old"}, {"a": "new"})
        assert result["a"] == "new"

    def test_empty_override_leaves_base_unchanged(self) -> None:
        base = {"a": 1, "b": 2}
        assert deep_merge(base, {}) == base

    def test_empty_base_returns_override(self) -> None:
        assert deep_merge({}, {"x": 42}) == {"x": 42}


class TestLoadConfig:
    def test_returns_defaults_when_no_files(self, tmp_path: Path) -> None:
        cfg = load_config(cwd=tmp_path)
        assert cfg.model == "deepseek/deepseek-v4.1-flash"
        assert cfg.approval_mode == "default"
        assert cfg.plan_mode is True

    def test_project_shared_overrides_user_global(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".agent").mkdir(parents=True)
        (home / ".agent" / "settings.json").write_text(json.dumps({"model": "user-model"}))

        project = tmp_path / "project"
        (project / ".agent").mkdir(parents=True)
        (project / ".agent" / "settings.json").write_text(json.dumps({"model": "project-model"}))

        import agent.config.config as mod
        original_home = Path.home
        Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
        try:
            cfg = load_config(cwd=project)
        finally:
            Path.home = staticmethod(original_home)  # type: ignore[method-assign]

        assert cfg.model == "project-model"

    def test_local_overrides_shared(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.json").write_text(json.dumps({"model": "shared-model"}))
        (agent_dir / "settings.local.json").write_text(json.dumps({"model": "local-model"}))

        cfg = load_config(cwd=tmp_path)
        assert cfg.model == "local-model"

    def test_cli_overrides_local(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.local.json").write_text(json.dumps({"model": "local-model"}))

        cfg = load_config(cwd=tmp_path, cli_overrides={"model": "cli-model"})
        assert cfg.model == "cli-model"

    def test_missing_files_silently_skipped(self, tmp_path: Path) -> None:
        cfg = load_config(cwd=tmp_path)
        assert isinstance(cfg, Config)

    def test_cli_none_values_ignored(self, tmp_path: Path) -> None:
        cfg = load_config(cwd=tmp_path, cli_overrides={"model": None})
        assert cfg.model == "deepseek/deepseek-v4.1-flash"

    def test_camel_case_keys_accepted(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.json").write_text(
            json.dumps({"approvalMode": "auto", "planMode": False, "webSearch": True})
        )
        cfg = load_config(cwd=tmp_path)
        assert cfg.approval_mode == "auto"
        assert cfg.plan_mode is False
        assert cfg.web_search is True

    def test_mcp_servers_parsed(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.json").write_text(
            json.dumps({
                "mcpServers": {
                    "git": {"command": ["uvx", "mcp-server-git", "--repository", "."], "env": {}}
                }
            })
        )
        cfg = load_config(cwd=tmp_path)
        assert "git" in cfg.mcp_servers
        assert cfg.mcp_servers["git"].command[0] == "uvx"

    def test_all_four_levels_priority_order(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".agent").mkdir(parents=True)
        (home / ".agent" / "settings.json").write_text(json.dumps({"model": "L1"}))

        project = tmp_path / "project"
        agent_dir = project / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "settings.json").write_text(json.dumps({"model": "L2"}))
        (agent_dir / "settings.local.json").write_text(json.dumps({"model": "L3"}))

        original_home = Path.home
        Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
        try:
            assert load_config(cwd=project).model == "L3"
            assert load_config(cwd=project, cli_overrides={"model": "L4"}).model == "L4"
        finally:
            Path.home = staticmethod(original_home)  # type: ignore[method-assign]

    def test_defaults_come_from_code_not_json(self, tmp_path: Path) -> None:
        # No settings files at all — defaults must come from Config dataclass
        cfg = load_config(cwd=tmp_path)
        assert cfg.max_tool_output_chars == 10_000
        assert cfg.max_retries == 3
        assert cfg.context_limit == 1_000_000
        assert cfg.auto_memory is True
