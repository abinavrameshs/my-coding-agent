"""Tests for the bash tool."""

from pathlib import Path

import pytest

from agent.tools.bash import run_bash
from agent.tools.files import ToolError


class TestRunBash:
    def test_basic_command(self, tmp_path: Path) -> None:
        out = run_bash("echo hello", tmp_path)
        assert "hello" in out

    def test_stderr_included(self, tmp_path: Path) -> None:
        out = run_bash("echo err >&2", tmp_path)
        assert "err" in out

    def test_output_truncated(self, tmp_path: Path) -> None:
        out = run_bash("python3 -c \"print('x' * 20000)\"", tmp_path, max_output_chars=100)
        assert "truncated" in out
        assert len(out) < 300

    def test_timeout_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError, match="timed out"):
            run_bash("sleep 60", tmp_path, timeout=1)

    def test_dangerous_pattern_prepends_warning(self, tmp_path: Path) -> None:
        out = run_bash("echo 'rm -rf test'", tmp_path)
        assert "warning" in out.lower()

    def test_no_output_placeholder(self, tmp_path: Path) -> None:
        out = run_bash("true", tmp_path)
        assert out  # non-empty
