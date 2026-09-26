"""Tests for file tools."""

from pathlib import Path

import pytest

from agent.tools.files import ToolError, edit_file, find_files, grep_files, read_file, write_file


@pytest.fixture
def wd(tmp_path: Path) -> Path:
    return tmp_path


class TestReadFile:
    def test_reads_file_with_line_numbers(self, wd: Path) -> None:
        (wd / "f.txt").write_text("alpha\nbeta\ngamma")
        out = read_file({"path": "f.txt"}, wd)
        assert "alpha" in out and "   1 |" in out

    def test_line_range(self, wd: Path) -> None:
        (wd / "f.txt").write_text("a\nb\nc\nd")
        out = read_file({"path": "f.txt", "start_line": 2, "end_line": 3}, wd)
        assert "b" in out and "c" in out and "a" not in out

    def test_missing_file_raises(self, wd: Path) -> None:
        with pytest.raises(ToolError):
            read_file({"path": "nope.txt"}, wd)

    def test_path_escape_rejected(self, wd: Path) -> None:
        with pytest.raises(ToolError):
            read_file({"path": "../../etc/passwd"}, wd)


class TestWriteFile:
    def test_creates_file(self, wd: Path) -> None:
        write_file({"path": "new.txt", "content": "hello"}, wd)
        assert (wd / "new.txt").read_text() == "hello"

    def test_creates_parent_dirs(self, wd: Path) -> None:
        write_file({"path": "a/b/c.txt", "content": "x"}, wd)
        assert (wd / "a" / "b" / "c.txt").exists()

    def test_returns_line_count(self, wd: Path) -> None:
        result = write_file({"path": "f.txt", "content": "a\nb\nc"}, wd)
        assert "3" in result


class TestEditFile:
    def test_replaces_unique_string(self, wd: Path) -> None:
        (wd / "f.py").write_text("def foo(): pass\n")
        edit_file({"path": "f.py", "old_string": "foo", "new_string": "bar"}, wd)
        assert "bar" in (wd / "f.py").read_text()

    def test_raises_if_not_found(self, wd: Path) -> None:
        (wd / "f.py").write_text("hello")
        with pytest.raises(ToolError, match="not found"):
            edit_file({"path": "f.py", "old_string": "xyz", "new_string": "abc"}, wd)

    def test_raises_if_multiple_matches(self, wd: Path) -> None:
        (wd / "f.py").write_text("foo foo")
        with pytest.raises(ToolError, match="2 times"):
            edit_file({"path": "f.py", "old_string": "foo", "new_string": "bar"}, wd)

    def test_path_escape_rejected(self, wd: Path) -> None:
        with pytest.raises(ToolError):
            edit_file({"path": "../../x", "old_string": "a", "new_string": "b"}, wd)


class TestGrepFiles:
    def test_finds_pattern(self, wd: Path) -> None:
        (wd / "a.py").write_text("def hello(): pass\n")
        out = grep_files({"pattern": "def hello"}, wd)
        assert "a.py" in out

    def test_no_matches(self, wd: Path) -> None:
        (wd / "a.py").write_text("nothing here")
        out = grep_files({"pattern": "XXXXXX"}, wd)
        assert "No matches" in out


class TestFindFiles:
    def test_finds_by_glob(self, wd: Path) -> None:
        (wd / "main.py").write_text("")
        (wd / "test.js").write_text("")
        out = find_files({"pattern": "*.py"}, wd)
        assert "main.py" in out
        assert "test.js" not in out

    def test_no_matches(self, wd: Path) -> None:
        out = find_files({"pattern": "*.go"}, wd)
        assert "No files found" in out
