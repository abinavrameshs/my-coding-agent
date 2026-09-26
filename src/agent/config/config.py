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
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel
from pydantic import ConfigDict

load_dotenv()

# ---------------------------------------------------------------------------
# Secrets / environment variables
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    command: list[str]
    env: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    """Full agent configuration. Accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,   # accept snake_case keys too (e.g. from CLI)
    )

    # LLM
    model: str = "deepseek/deepseek-v4.1-flash"
    base_url: str = Field(default=OPENROUTER_BASE_URL)

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
    webhook_events: list[str] = Field(default_factory=list)

    # MCP servers
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)

    # Tool allow/deny lists
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)

    # JEV decisions model (used for tool routing, approval, memory)
    jev_model: str = "~typesafe/jev-latest"
    jev_endpoint: str = "https://openrouter.ai/api/alpha/decisions"
    jev_threshold: float = 0.6   # noul probability above which answer is True
    jev_timeout: float = 8.0     # seconds before JEV call is abandoned


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

    # CLI flags are highest priority; skip None (flag not passed)
    if cli_overrides:
        merged = deep_merge(merged, {k: v for k, v in cli_overrides.items() if v is not None})

    return Config.model_validate(merged)
