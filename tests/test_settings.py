"""Tests for the 4-level settings hierarchy."""

import json
from pathlib import Path

import pytest

from agent.config.settings import Settings, deep_merge, load_settings


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


class TestLoadSettings:
    def test_returns_defaults_when_no_files(self, tmp_path: Path) -> None:
        settings = load_settings(cwd=tmp_path)
        assert settings.model == "deepseek/deepseek-v4.1-flash"
        assert settings.approval_mode == "default"
        assert settings.plan_mode is True

    def test_project_shared_overrides_user_global(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "home" / ".agent"
        user_dir.mkdir(parents=True)
        (user_dir / "settings.json").write_text(json.dumps({"model": "user-model"}))

        project_dir = tmp_path / "project" / ".agent"
        project_dir.mkdir(parents=True)
        (project_dir / "settings.json").write_text(json.dumps({"model": "project-model"}))

        # Patch home to our fake home
        import agent.config.settings as mod
        original_home = Path.home

        Path.home = staticmethod(lambda: tmp_path / "home")  # type: ignore[method-assign]
        try:
            settings = load_settings(cwd=tmp_path / "project")
        finally:
            Path.home = staticmethod(original_home)  # type: ignore[method-assign]

        assert settings.model == "project-model"

    def test_local_overrides_shared(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.json").write_text(json.dumps({"model": "shared-model"}))
        (agent_dir / "settings.local.json").write_text(json.dumps({"model": "local-model"}))

        settings = load_settings(cwd=tmp_path)
        assert settings.model == "local-model"

    def test_cli_overrides_local(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.local.json").write_text(json.dumps({"model": "local-model"}))

        settings = load_settings(cwd=tmp_path, cli_overrides={"model": "cli-model"})
        assert settings.model == "cli-model"

    def test_missing_files_silently_skipped(self, tmp_path: Path) -> None:
        settings = load_settings(cwd=tmp_path)
        assert isinstance(settings, Settings)

    def test_cli_none_values_ignored(self, tmp_path: Path) -> None:
        settings = load_settings(cwd=tmp_path, cli_overrides={"model": None})
        assert settings.model == "deepseek/deepseek-v4.1-flash"

    def test_camel_case_keys_accepted(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.json").write_text(
            json.dumps({"approvalMode": "auto", "planMode": False, "webSearch": True})
        )
        settings = load_settings(cwd=tmp_path)
        assert settings.approval_mode == "auto"
        assert settings.plan_mode is False
        assert settings.web_search is True

    def test_mcp_servers_parsed(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "settings.json").write_text(
            json.dumps({
                "mcp_servers": {
                    "git": {"command": ["uvx", "mcp-server-git", "--repository", "."], "env": {}}
                }
            })
        )
        settings = load_settings(cwd=tmp_path)
        assert "git" in settings.mcp_servers
        assert settings.mcp_servers["git"].command[0] == "uvx"

    def test_all_four_levels_priority_order(self, tmp_path: Path) -> None:
        """Higher-level sources must beat lower ones for the same key."""
        import agent.config.settings as mod
        original_home = Path.home

        home = tmp_path / "home"
        (home / ".agent").mkdir(parents=True)
        (home / ".agent" / "settings.json").write_text(json.dumps({"model": "L1"}))

        project = tmp_path / "project"
        agent_dir = project / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "settings.json").write_text(json.dumps({"model": "L2"}))
        (agent_dir / "settings.local.json").write_text(json.dumps({"model": "L3"}))

        Path.home = staticmethod(lambda: home)  # type: ignore[method-assign]
        try:
            s_no_cli = load_settings(cwd=project)
            assert s_no_cli.model == "L3"

            s_with_cli = load_settings(cwd=project, cli_overrides={"model": "L4"})
            assert s_with_cli.model == "L4"
        finally:
            Path.home = staticmethod(original_home)  # type: ignore[method-assign]
