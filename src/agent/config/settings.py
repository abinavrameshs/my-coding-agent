"""4-level settings hierarchy loader.

Merge order (lowest → highest priority):
  ~/.agent/settings.json          (user-global)
  .agent/settings.json            (project-shared, committed)
  .agent/settings.local.json      (project-local, gitignored)
  CLI flags                       (passed as a dict to load_settings)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPServerConfig:
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Settings:
    model: str = "deepseek/deepseek-v4.1-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    approval_mode: str = "default"       # default | acceptEdits | auto | bypassPermissions
    plan_mode: bool = True
    web_search: bool = False
    auto_memory: bool = True
    max_tool_output_chars: int = 10_000
    max_retries: int = 3
    context_limit: int = 1_000_000       # tokens; compaction fires at 80%
    system_file: str | None = None
    webhook_url: str | None = None
    webhook_events: list[str] = field(default_factory=list)
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*; override wins on scalar conflicts."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_json(path: Path) -> dict[str, Any]:
    """Return parsed JSON from *path*, or empty dict if file is absent/invalid."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _snake(key: str) -> str:
    """Convert camelCase JSON keys to snake_case for dataclass fields."""
    import re
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _apply_dict(settings: Settings, data: dict[str, Any]) -> Settings:
    """Overlay *data* (camelCase or snake_case) onto a Settings copy."""
    for raw_key, val in data.items():
        attr = _snake(raw_key)
        if attr == "mcp_servers" and isinstance(val, dict):
            servers: dict[str, MCPServerConfig] = {}
            for name, cfg in val.items():
                if isinstance(cfg, dict):
                    servers[name] = MCPServerConfig(
                        command=cfg.get("command", []),
                        env=cfg.get("env", {}),
                    )
            object.__setattr__(settings, "mcp_servers", servers)
        elif hasattr(settings, attr):
            object.__setattr__(settings, attr, val)
    return settings


def load_settings(
    cwd: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Settings:
    """Load and merge settings from all four levels.

    Args:
        cwd: Project directory (defaults to Path.cwd()).
        cli_overrides: Dict of snake_case keys from CLI flags (None values ignored).
    """
    cwd = cwd or Path.cwd()

    sources: list[Path] = [
        Path.home() / ".agent" / "settings.json",   # 1. user-global
        cwd / ".agent" / "settings.json",            # 2. project-shared
        cwd / ".agent" / "settings.local.json",      # 3. project-local
    ]

    merged: dict[str, Any] = {}
    for path in sources:
        merged = deep_merge(merged, _load_json(path))

    settings = Settings()
    _apply_dict(settings, merged)

    # 4. CLI flags (highest priority; skip None values)
    if cli_overrides:
        clean = {k: v for k, v in cli_overrides.items() if v is not None}
        _apply_dict(settings, clean)

    return settings
