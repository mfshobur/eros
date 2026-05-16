"""Tests for /export command."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch


def _make_turns(n=2):
    return [
        {
            "ts": f"2026-01-0{i+1}T10:00:00",
            "model": "ollama/test",
            "user": f"user message {i+1}",
            "assistant": f"ai response {i+1}",
        }
        for i in range(n)
    ]


class TestExportCommand:
    def _make_state(self, room="test-room"):
        return {"room": room}

    def _make_agent(self):
        agent = MagicMock()
        agent.config = {}
        agent.history = []
        return agent

    def test_export_creates_markdown_file(self, tmp_path, monkeypatch):
        from main import handle_slash_command
        import memory.rooms as rooms
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rooms, "load_turns", lambda room, max_turns: _make_turns(2))
        handle_slash_command("/export myexport", self._make_agent(), self._make_state())
        out = (tmp_path / "myexport.md").read_text()
        assert "user message 1" in out
        assert "ai response 1" in out
        assert "test-room" in out

    def test_export_appends_md_extension(self, tmp_path, monkeypatch):
        from main import handle_slash_command
        import memory.rooms as rooms
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rooms, "load_turns", lambda room, max_turns: _make_turns(1))
        handle_slash_command("/export noext", self._make_agent(), self._make_state())
        assert (tmp_path / "noext.md").exists()

    def test_export_default_filename_contains_room(self, tmp_path, monkeypatch):
        from main import handle_slash_command
        import memory.rooms as rooms
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rooms, "load_turns", lambda room, max_turns: _make_turns(1))
        handle_slash_command("/export", self._make_agent(), self._make_state("my-room"))
        files = list(tmp_path.glob("my-room-*.md"))
        assert len(files) == 1

    def test_export_empty_room_prints_error(self, capsys, tmp_path, monkeypatch):
        from main import handle_slash_command
        import memory.rooms as rooms
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rooms, "load_turns", lambda room, max_turns: [])
        handle_slash_command("/export", self._make_agent(), self._make_state())
        out = capsys.readouterr().out
        assert "No conversation" in out

    def test_export_includes_tool_calls(self, tmp_path, monkeypatch):
        from main import handle_slash_command
        import memory.rooms as rooms
        monkeypatch.chdir(tmp_path)
        turns = _make_turns(1)
        turns[0]["tools"] = [{"name": "bash", "args": {"command": "ls"}}]
        monkeypatch.setattr(rooms, "load_turns", lambda room, max_turns: turns)
        handle_slash_command("/export tooltest", self._make_agent(), self._make_state())
        out = (tmp_path / "tooltest.md").read_text()
        assert "bash" in out
