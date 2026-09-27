"""Skills loader — discovers and invokes SKILL.md files from project + user skill dirs.

Discovery order (lowest → highest priority):
  1. <cwd>/.agent/skills/<name>/SKILL.md  (project multi-file)
  2. <cwd>/.agent/commands/<name>.md      (project flat commands — highest priority)

Each SKILL.md / command .md has optional YAML frontmatter:
  ---
  name: skill-name
  description: One-line description
  allowed-tools: [bash, read_file]   # optional; restricts tools for this skill
  ---
  Skill instructions…

Shell injection: backtick expressions !`cmd` in the body are executed and
replaced with the command's stdout before the content is sent to the model.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_SHELL_INJECT_RE = re.compile(r"!`([^`]+)`")


def _skill_dirs(cwd: Path) -> list[Path]:
    return [
        cwd / ".agent" / "skills",
    ]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from body. Returns ({meta}, body_text)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_meta = m.group(1)
    body = text[m.end():]
    meta: dict[str, Any] = {}
    if _YAML_AVAILABLE:
        try:
            meta = yaml.safe_load(raw_meta) or {}
        except Exception:
            meta = {}
    else:
        # Minimal key: value parser (no nested structures)
        for line in raw_meta.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
    return meta, body


def _inject_shell(text: str, cwd: Path) -> str:
    """Replace !`cmd` occurrences with the command's stdout."""
    def _run(match: re.Match) -> str:  # type: ignore[type-arg]
        cmd = match.group(1)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, cwd=str(cwd), timeout=10
            )
            return result.stdout.rstrip("\n")
        except Exception as e:
            return f"(shell injection failed: {e})"

    return _SHELL_INJECT_RE.sub(_run, text)


class Skill:
    def __init__(self, name: str, description: str, body: str, meta: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.body = body
        self.meta = meta
        self.allowed_tools: list[str] = meta.get("allowed-tools") or []

    def render(self, cwd: Path) -> str:
        """Return the skill body with shell injections resolved."""
        return _inject_shell(self.body, cwd)


def _load_skill_from_file(path: Path, default_name: str) -> Skill:
    """Parse a single skill file (flat .md or SKILL.md) and return a Skill."""
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    name = str(meta.get("name") or default_name).lower().strip()
    description = str(meta.get("description") or "")
    return Skill(name=name, description=description, body=body, meta=meta)


def discover_skills(cwd: Path) -> dict[str, Skill]:
    """Return a {name: Skill} dict from all skill locations. Later entries win.

    Discovery order (lowest → highest priority):
      1. <cwd>/.agent/skills/<name>/SKILL.md  (project multi-file)
      2. <cwd>/.agent/commands/<name>.md      (project flat commands — highest priority)
    """
    found: dict[str, Skill] = {}

    # 1 → 2: skill directories, user first so project overwrites
    for skills_root in _skill_dirs(cwd):
        if not skills_root.exists():
            continue
        for skill_dir in sorted(skills_root.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            skill = _load_skill_from_file(skill_file, skill_dir.name)
            found[skill.name] = skill

    # 3: .agent/commands/*.md — flat single-file commands; highest priority
    commands_dir = cwd / ".agent" / "commands"
    if commands_dir.exists():
        for md_file in sorted(commands_dir.glob("*.md")):
            default_name = md_file.stem.lower()
            skill = _load_skill_from_file(md_file, default_name)
            found[skill.name] = skill

    return found


# Built-in /init skill
_INIT_SKILL_BODY = """\
Analyze the project structure and generate a concise AGENT.md file at the
project root. The file should describe:

1. What this project is and does (1-2 sentences)
2. Key directories and their purpose
3. How to run, test, and build the project
4. Any important conventions or constraints a developer should know

Read the README, pyproject.toml/package.json/Makefile (if present), and do a
quick `find` to understand the structure. Then write AGENT.md.
"""

BUILTIN_SKILLS: dict[str, Skill] = {
    "init": Skill(
        name="init",
        description="Generate an AGENT.md for this project",
        body=_INIT_SKILL_BODY,
        meta={},
    ),
}
