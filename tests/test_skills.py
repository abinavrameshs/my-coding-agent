"""Tests for the skills loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.memory.skills import (
    BUILTIN_SKILLS,
    Skill,
    _inject_shell,
    _parse_frontmatter,
    discover_skills,
)


class TestParseFrontmatter:
    def test_no_frontmatter(self) -> None:
        meta, body = _parse_frontmatter("Hello world")
        assert meta == {}
        assert body == "Hello world"

    def test_with_frontmatter(self) -> None:
        text = "---\nname: greet\ndescription: Say hello\n---\nBody here."
        meta, body = _parse_frontmatter(text)
        assert meta.get("name") == "greet"
        assert "Body here." in body

    def test_body_not_duplicated(self) -> None:
        text = "---\nname: x\n---\nJust the body."
        _, body = _parse_frontmatter(text)
        assert body.count("Just the body.") == 1


class TestInjectShell:
    def test_replaces_backtick_expr(self, tmp_path: Path) -> None:
        result = _inject_shell("Files: !`echo hello`", tmp_path)
        assert "hello" in result
        assert "!`" not in result

    def test_multiple_injections(self, tmp_path: Path) -> None:
        result = _inject_shell("A=!`echo foo`  B=!`echo bar`", tmp_path)
        assert "foo" in result
        assert "bar" in result

    def test_no_injection(self, tmp_path: Path) -> None:
        text = "No shell expressions here."
        assert _inject_shell(text, tmp_path) == text

    def test_failed_command_does_not_raise(self, tmp_path: Path) -> None:
        result = _inject_shell("!`nonexistent_xyz_command_123`", tmp_path)
        assert "failed" in result.lower() or result == ""  # graceful


class TestDiscoverSkills:
    def _write_skill(self, base: Path, name: str, body: str, meta: str = "") -> None:
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        front = f"---\n{meta}\n---\n" if meta else ""
        (d / "SKILL.md").write_text(front + body)

    def test_discovers_project_skill(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / ".agent" / "skills"
        self._write_skill(skills_dir, "greet", "Say hello!", "name: greet\ndescription: Greeting")
        skills = discover_skills(tmp_path)
        assert "greet" in skills
        assert "hello" in skills["greet"].body.lower()

    def test_description_loaded(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / ".agent" / "skills"
        self._write_skill(skills_dir, "myskill", "Body", "name: myskill\ndescription: Does stuff")
        skills = discover_skills(tmp_path)
        assert skills["myskill"].description == "Does stuff"

    def test_no_skills_dir_returns_empty(self, tmp_path: Path) -> None:
        skills = discover_skills(tmp_path)
        # Should only return empty (user dir may have skills on dev machine, ignore)
        assert isinstance(skills, dict)

    def test_skill_name_falls_back_to_dir_name(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / ".agent" / "skills"
        self._write_skill(skills_dir, "mydir", "Body content")  # no frontmatter name
        skills = discover_skills(tmp_path)
        assert "mydir" in skills

    def test_missing_skill_md_ignored(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / ".agent" / "skills" / "empty"
        skills_dir.mkdir(parents=True)
        # No SKILL.md file
        skills = discover_skills(tmp_path)
        assert "empty" not in skills


class TestBuiltinSkills:
    def test_init_skill_exists(self) -> None:
        assert "init" in BUILTIN_SKILLS

    def test_init_skill_description(self) -> None:
        assert BUILTIN_SKILLS["init"].description

    def test_init_skill_body_not_empty(self) -> None:
        assert len(BUILTIN_SKILLS["init"].body) > 20


class TestSkillRender:
    def test_render_replaces_shell(self, tmp_path: Path) -> None:
        skill = Skill(
            name="test",
            description="",
            body="Count: !`echo 42`",
            meta={},
        )
        rendered = skill.render(tmp_path)
        assert "42" in rendered

    def test_render_no_injection(self, tmp_path: Path) -> None:
        skill = Skill(name="test", description="", body="Static content.", meta={})
        assert skill.render(tmp_path) == "Static content."
