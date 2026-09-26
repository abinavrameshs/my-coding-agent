"""Agent configuration.

Defaults are defined here in code.
Secrets and env-specific values come from .env (loaded via python-dotenv).
Project/user overrides come from the 4-level settings file hierarchy:
  ~/.agent/settings.json          (user-global)
  .agent/settings.json            (project-shared, committed)
  .agent/settings.local.json      (project-local, gitignored)
  CLI flags                       (highest priority)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Secrets / environment variables
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)

# ---------------------------------------------------------------------------
# Settings dataclass (all defaults live here, not in JSON files)
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    # LLM
    model: str = "deepseek/deepseek-v4.1-flash"
    base_url: str = OPENROUTER_BASE_URL

    # Behaviour
    approval_mode: str = "default"   # default | acceptEdits | auto | bypassPermissions
    plan_mode: bool = True
    web_search: bool = False
    auto_memory: bool = True

    # Limits
    max_tool_output_chars: int = 10_000
    max_retries: int = 3
    context_limit: int = 1_000_000   # tokens; compaction fires at 80%

    # Optional overrides
    system_file: str | None = None
    webhook_url: str | None = None
    webhook_events: list[str] = field(default_factory=list)

    # MCP servers
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)

    # Tool allow/deny lists
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _to_snake(key: str) -> str:
    """Convert camelCase JSON keys to snake_case."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _apply(cfg: Config, data: dict[str, Any]) -> None:
    """Overlay *data* (camelCase or snake_case) onto *cfg* in place."""
    for raw_key, val in data.items():
        attr = _to_snake(raw_key)
        if attr == "mcp_servers" and isinstance(val, dict):
            servers: dict[str, MCPServerConfig] = {}
            for name, spec in val.items():
                if isinstance(spec, dict):
                    servers[name] = MCPServerConfig(
                        command=spec.get("command", []),
                        env=spec.get("env", {}),
                    )
            object.__setattr__(cfg, "mcp_servers", servers)
        elif hasattr(cfg, attr):
            object.__setattr__(cfg, attr, val)


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_config(
    cwd: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> Config:
    """Build a Config by merging the 4-level hierarchy onto hardcoded defaults.

    Args:
        cwd: Project directory (defaults to Path.cwd()).
        cli_overrides: Dict of snake_case keys from CLI flags; None values are skipped.
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

    cfg = Config()
    _apply(cfg, merged)

    # CLI flags are highest priority; skip None (flag not passed)
    if cli_overrides:
        _apply(cfg, {k: v for k, v in cli_overrides.items() if v is not None})

    return cfg
