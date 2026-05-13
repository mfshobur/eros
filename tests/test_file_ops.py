"""Tests for file_ops tools: write, read, edit, append, list."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tempfile
import pytest
from tools.base import load_tools, dispatch_tool

load_tools(["file_ops"])


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


class TestWriteRead:
    def test_write_then_read(self, tmp):
        path = str(tmp / "out.txt")
        dispatch_tool("write_file", {"path": path, "content": "hello world"})
        result = dispatch_tool("read_file", {"path": path})
        assert "hello world" in result

    def test_write_overwrites(self, tmp):
        path = str(tmp / "out.txt")
        dispatch_tool("write_file", {"path": path, "content": "first"})
        dispatch_tool("write_file", {"path": path, "content": "second"})
        result = dispatch_tool("read_file", {"path": path})
        assert "second" in result
        assert "first" not in result

    def test_read_missing_file(self, tmp):
        result = dispatch_tool("read_file", {"path": str(tmp / "nope.txt")})
        assert "Error" in result or "not found" in result.lower() or "No such file" in result


class TestAppend:
    def test_append_adds_content(self, tmp):
        path = str(tmp / "log.txt")
        dispatch_tool("write_file", {"path": path, "content": "line1\n"})
        dispatch_tool("append_file", {"path": path, "content": "line2\n"})
        result = dispatch_tool("read_file", {"path": path})
        assert "line1" in result
        assert "line2" in result


class TestEdit:
    def test_edit_replaces_string(self, tmp):
        path = str(tmp / "code.py")
        dispatch_tool("write_file", {"path": path, "content": "x = old_value\n"})
        dispatch_tool("edit_file", {"path": path, "old_string": "old_value", "new_string": "new_value"})
        result = dispatch_tool("read_file", {"path": path})
        assert "new_value" in result
        assert "old_value" not in result

    def test_edit_missing_string_returns_error(self, tmp):
        path = str(tmp / "code.py")
        dispatch_tool("write_file", {"path": path, "content": "x = 1\n"})
        result = dispatch_tool("edit_file", {"path": path, "old_string": "not_here", "new_string": "x"})
        assert "not found" in result.lower() or "Error" in result


class TestListDir:
    def test_list_shows_files(self, tmp):
        (tmp / "a.txt").write_text("a")
        (tmp / "b.py").write_text("b")
        result = dispatch_tool("list_dir", {"path": str(tmp)})
        assert "a.txt" in result
        assert "b.py" in result
