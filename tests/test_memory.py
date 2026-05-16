"""Tests for long-term memory helpers."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from pathlib import Path


@pytest.fixture
def mem_file(tmp_path, monkeypatch):
    import memory.rooms as rooms
    f = tmp_path / "memory.md"
    monkeypatch.setattr(rooms, "_MEMORY_FILE", f)
    return f


class TestLoadMemories:
    def test_returns_empty_when_no_file(self, mem_file):
        from memory.rooms import load_memories
        assert load_memories() == []

    def test_returns_key_value_lines(self, mem_file):
        from memory.rooms import load_memories
        mem_file.write_text("name: Shobur\npreferred_lang: TypeScript\n")
        assert load_memories() == ["name: Shobur", "preferred_lang: TypeScript"]

    def test_ignores_lines_without_colon(self, mem_file):
        from memory.rooms import load_memories
        mem_file.write_text("name: Shobur\njust a plain line\nkey: val\n")
        assert load_memories() == ["name: Shobur", "key: val"]


class TestSaveMemory:
    def test_saves_entry(self, mem_file):
        from memory.rooms import save_memory, load_memories
        assert save_memory("name: Shobur") is True
        assert load_memories() == ["name: Shobur"]

    def test_respects_cap(self, mem_file):
        from memory.rooms import save_memory
        save_memory("a: 1", max_memories=2)
        save_memory("b: 2", max_memories=2)
        result = save_memory("c: 3", max_memories=2)
        assert result is False

    def test_allows_up_to_cap(self, mem_file):
        from memory.rooms import save_memory, load_memories
        save_memory("a: 1", max_memories=2)
        save_memory("b: 2", max_memories=2)
        assert len(load_memories()) == 2


class TestDeleteMemory:
    def test_removes_matching_entries(self, mem_file):
        from memory.rooms import save_memory, delete_memory, load_memories
        save_memory("name: Shobur")
        save_memory("lang: TypeScript")
        n = delete_memory("Shobur")
        assert n == 1
        assert load_memories() == ["lang: TypeScript"]

    def test_returns_zero_when_no_match(self, mem_file):
        from memory.rooms import save_memory, delete_memory
        save_memory("name: Shobur")
        assert delete_memory("nobody") == 0

    def test_returns_zero_when_no_file(self, mem_file):
        from memory.rooms import delete_memory
        assert delete_memory("anything") == 0


class TestMemoryInSystemPrompt:
    def test_injected_when_memories_exist(self, mem_file):
        from memory.rooms import save_memory
        from agent import _load_memories
        save_memory("name: Shobur")
        result = _load_memories()
        assert "Memory" in result
        assert "name: Shobur" in result

    def test_empty_when_no_memories(self, mem_file):
        from agent import _load_memories
        assert _load_memories() == ""
