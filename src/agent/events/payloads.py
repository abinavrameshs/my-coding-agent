"""Pydantic models for every event payload emitted on the agent bus.

Using typed models instead of raw dicts catches key-name mismatches at
construction time (a typo like ``{"message": "..."}`` instead of
``{"error": "..."}`` becomes a validation error before it can silently
reach the wrong handler).

Mutable fields (``cancelled``, ``updated_input``, ``updated_plan``) are
intentionally writable — approval and plan listeners set them on the live
event object; the agent loop reads them back after ``await bus.emit(...)``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EmptyPayload(BaseModel):
    """Default payload for events that carry no data."""


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class SessionStartPayload(BaseModel):
    model: str = ""
    session_id: str = ""


class SessionEndPayload(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Turn lifecycle
# ---------------------------------------------------------------------------


class TurnStartPayload(BaseModel):
    message_count: int = 0


class TurnEndPayload(BaseModel):
    usage: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class StreamDeltaPayload(BaseModel):
    text: str


class MessageAssistantPayload(BaseModel):
    text: str = ""
    usage: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Tool lifecycle
# ---------------------------------------------------------------------------


class ToolBeforePayload(BaseModel):
    """Mutable: approval listener sets ``cancelled`` / ``updated_input``."""

    tool: str
    input: dict[str, Any] = {}
    call_id: str = ""
    cancelled: bool = False
    updated_input: dict[str, Any] | None = None


class ToolAfterPayload(BaseModel):
    tool: str
    call_id: str = ""
    output: str = ""
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Plan mode
# ---------------------------------------------------------------------------


class PlanProposedPayload(BaseModel):
    """Mutable: plan listener sets ``cancelled`` / ``updated_plan``."""

    plan: str
    cancelled: bool = False
    updated_plan: str | None = None


class PlanApprovedPayload(BaseModel):
    plan: str


class PlanCancelledPayload(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class ContextCompactPayload(BaseModel):
    tokens_before: int = 0
    msgs_before: int = 0
    msgs_after: int = 0


# ---------------------------------------------------------------------------
# Subagents
# ---------------------------------------------------------------------------


class SubagentStartPayload(BaseModel):
    subagent_id: str
    prompt: str = ""


class SubagentEndPayload(BaseModel):
    subagent_id: str
    result: str = ""


class ParallelBatchPayload(BaseModel):
    tasks: list[str] = []


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


class MCPServerStartPayload(BaseModel):
    server: str
    tools: int = 0
    transport: str = ""


class MCPReadyPayload(BaseModel):
    servers: dict[str, int] = {}
    total_tools: int = 0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorPayload(BaseModel):
    error: str
