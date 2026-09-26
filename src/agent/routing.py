"""JEV-powered router for lightweight decisions that don't need text generation.

Uses the decisions model (default: ~typesafe/jev-latest) configured in Config
(cfg.jev_model / cfg.jev_endpoint / cfg.jev_threshold / cfg.jev_timeout) to:
  - Select which tool groups to activate for a user request
  - Decide whether a bash command needs explicit approval
  - Decide whether a session is worth extracting memory from
  - Detect whether a user message expresses intent to remember a fact
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from agent.config.config import Config
    from agent.tools.registry import ToolRegistry

class JEVClient:
    def __init__(self, api_key: str, cfg: "Config") -> None:
        self._key = api_key
        self._cfg = cfg
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/my-coding-agent",
            "X-Title": "my-coding-agent",
        }

    async def decide(
        self,
        state: str,
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Call the decisions endpoint and return the raw answers dict.

        Returns empty dict on any error so callers always get a safe fallback.
        """
        try:
            async with httpx.AsyncClient(timeout=self._cfg.jev_timeout) as client:
                r = await client.post(
                    self._cfg.jev_endpoint,
                    headers=self._headers,
                    json={
                        "model": self._cfg.jev_model,
                        "state": state,
                        "questions": questions,
                    },
                )
                if r.status_code == 200:
                    return r.json().get("answers", {})
        except Exception:
            pass
        return {}

    def _noul(self, answers: dict, key: str) -> bool:
        """Extract a boolean from a noul answer (True when probability >= threshold)."""
        ans = answers.get(key, {})
        return float(ans.get("noul", 0)) >= self._cfg.jev_threshold


# ---------------------------------------------------------------------------
# Tool group routing
# ---------------------------------------------------------------------------


async def auto_enable_groups(
    user_message: str,
    registry: "ToolRegistry",
    cwd: Path,
    cfg: "Config",
    console: Any | None = None,
) -> None:
    """Ask JEV which tool groups this request needs and enable them silently.

    Skips groups that are already active. On JEV failure, does nothing —
    the core tools (file + bash) are always available as a fallback.
    """
    from agent.config.config import OPENROUTER_API_KEY
    from agent.tools.registry import ToolGroup

    already_active = registry.active_groups()

    # Build context about what's available to enable
    available: dict[str, str] = {}
    if ToolGroup.GIT not in already_active and (cwd / ".git").exists():
        available["needs_git"] = "git status, diff, commit, log, branch, checkout"
    if ToolGroup.WEB not in already_active and not cfg.web_search:
        available["needs_web"] = "web search and URL fetching for external docs/versions"
    if ToolGroup.MCP not in already_active and registry.mcp:
        non_git = [
            t["function"]["name"]
            for t in registry.mcp.tool_list()
            if not t["function"]["name"].startswith("mcp__git__")
        ]
        if non_git:
            available["needs_mcp"] = f"custom MCP tools: {', '.join(non_git[:3])}"

    if not available:
        return  # nothing to evaluate

    questions: dict[str, dict[str, Any]] = {
        key: {
            "type": "noul",
            "instructions": f"Does this user request need: {description}?",
            "criteria": {
                True: f"Request involves {description}",
                False: "Core file and bash tools are sufficient",
            },
        }
        for key, description in available.items()
    }

    client = JEVClient(OPENROUTER_API_KEY, cfg)
    answers = await client.decide(
        state=f"User request: {user_message[:600]}",
        questions=questions,
    )

    enabled: list[str] = []
    if client._noul(answers, "needs_git"):
        registry.enable(ToolGroup.GIT)
        enabled.append("git")
    if client._noul(answers, "needs_web"):
        registry.enable(ToolGroup.WEB)
        enabled.append("web")
    if client._noul(answers, "needs_mcp"):
        registry.enable(ToolGroup.MCP)
        enabled.append("mcp")

    if enabled and console:
        groups = " + ".join(enabled)
        console.print(f"[dim]⚡ auto-enabled: {groups} tools[/dim]")


# ---------------------------------------------------------------------------
# Approval routing
# ---------------------------------------------------------------------------


async def should_approve_bash(
    command: str,
    cfg: "Config",
) -> bool:
    """Ask JEV whether a bash command is safe to auto-approve.

    Returns True (safe, approve) or False (risky, prompt user).
    Falls back to False (prompt) on any error.
    """
    from agent.config.config import OPENROUTER_API_KEY

    client = JEVClient(OPENROUTER_API_KEY, cfg)
    answers = await client.decide(
        state=f"Shell command to execute: {command}",
        questions={
            "is_safe": {
                "type": "noul",
                "instructions": "Is this shell command safe to run without user approval?",
                "criteria": {
                    True: "Read-only, non-destructive, reversible, or standard dev command",
                    False: "Deletes files, modifies system, sends data externally, or is destructive",
                },
            }
        },
    )
    return client._noul(answers, "is_safe")


# ---------------------------------------------------------------------------
# Memory worthiness
# ---------------------------------------------------------------------------


async def should_extract_memory(
    turn_count: int,
    last_user_message: str,
    cfg: "Config",
) -> bool:
    """Ask JEV whether this session has learnings worth storing in memory.

    Skips very short sessions to avoid noise.
    """
    if turn_count < 3:
        return False

    from agent.config.config import OPENROUTER_API_KEY

    client = JEVClient(OPENROUTER_API_KEY, cfg)
    answers = await client.decide(
        state=f"Session had {turn_count} turns. Last message: {last_user_message[:200]}",
        questions={
            "worth_remembering": {
                "type": "noul",
                "instructions": "Did this session likely surface project-specific facts worth remembering?",
                "criteria": {
                    True: "Session involved project conventions, preferences, or decisions",
                    False: "Session was a simple one-off question or exploratory task",
                },
            }
        },
    )
    return client._noul(answers, "worth_remembering")


# ---------------------------------------------------------------------------
# Memory intent detection
# ---------------------------------------------------------------------------


async def has_memory_intent(
    user_message: str,
    cfg: "Config",
) -> bool:
    """Ask JEV whether the user message expresses intent to store a persistent fact.

    Returns True for messages like "my name is X", "always use httpx",
    "remember that I prefer tabs", "use this going forward".
    Returns False for task instructions, questions, or general conversation.
    """
    from agent.config.config import OPENROUTER_API_KEY

    client = JEVClient(OPENROUTER_API_KEY, cfg)
    answers = await client.decide(
        state=f"User message to a coding assistant: {user_message[:400]}",
        questions={
            "wants_remembered": {
                "type": "noul",
                "instructions": (
                    "Does this message express that the user wants the assistant to "
                    "persistently remember a personal fact, name, or preference for "
                    "future sessions?"
                ),
                "criteria": {
                    True: (
                        "User states their name, a library preference, a coding style, "
                        "or uses phrases like 'remember this', 'going forward', 'always use', "
                        "'store this', 'my name is', 'I prefer'"
                    ),
                    False: (
                        "Message is a task instruction, a question, a correction, or "
                        "general conversation with no persistent fact to store"
                    ),
                },
            }
        },
    )
    return client._noul(answers, "wants_remembered")
