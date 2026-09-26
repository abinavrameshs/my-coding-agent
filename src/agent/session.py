"""Session ID management and project-local storage paths.

All runtime data lives inside the project's .agent/ directory:
  .agent/sessions/     — saved conversation history (gitignored)
  .agent/transcripts/  — JSONL event logs (gitignored)
  .agent/memory.json   — persistent project facts (committable)
"""

from __future__ import annotations

import uuid
from pathlib import Path


def new_session_id() -> str:
    return uuid.uuid4().hex[:8]


def sessions_dir(cwd: Path) -> Path:
    d = cwd / ".agent" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcripts_dir(cwd: Path) -> Path:
    d = cwd / ".agent" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d
