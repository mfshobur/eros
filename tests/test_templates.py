"""Tests for prompt template helpers (/tsave, /t, /templates, /tdelete)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import pytest


@pytest.fixture
def tpl_file(tmp_path, monkeypatch):
    import memory.rooms as rooms
    f = tmp_path / "templates.jsonl"
    monkeypatch.setattr(rooms, "_TEMPLATES_FILE", f)
    return f


class TestLoadTemplates:
    def test_returns_empty_when_no_file(self, tpl_file):
        from memory.rooms import load_templates
        assert load_templates() == []

    def test_reads_saved_entries(self, tpl_file):
        from memory.rooms import load_templates
        tpl_file.write_text(
            json.dumps({"name": "review", "prompt": "Review this code"}) + "\n"
        )
        assert load_templates() == [{"name": "review", "prompt": "Review this code"}]

    def test_skips_malformed_lines(self, tpl_file):
        from memory.rooms import load_templates
        tpl_file.write_text(
            json.dumps({"name": "ok", "prompt": "fine"}) + "\n"
            "not valid json\n"
        )
        assert load_templates() == [{"name": "ok", "prompt": "fine"}]


class TestSaveTemplate:
    def test_saves_and_persists(self, tpl_file):
        from memory.rooms import save_template, load_templates
        save_template("greet", "Say hello")
        assert load_templates() == [{"name": "greet", "prompt": "Say hello"}]
        assert tpl_file.exists()

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        import memory.rooms as rooms
        nested = tmp_path / "deep" / "nested" / "templates.jsonl"
        monkeypatch.setattr(rooms, "_TEMPLATES_FILE", nested)
        rooms.save_template("a", "b")
        assert nested.exists()

    def test_overwrites_same_name(self, tpl_file):
        from memory.rooms import save_template, load_templates
        save_template("dup", "first")
        save_template("dup", "second")
        templates = load_templates()
        assert templates == [{"name": "dup", "prompt": "second"}]

    def test_keeps_other_templates(self, tpl_file):
        from memory.rooms import save_template, load_templates
        save_template("one", "1")
        save_template("two", "2")
        names = {t["name"] for t in load_templates()}
        assert names == {"one", "two"}


class TestGetTemplate:
    def test_returns_prompt(self, tpl_file):
        from memory.rooms import save_template, get_template
        save_template("bug", "Find the bug")
        assert get_template("bug") == "Find the bug"

    def test_returns_none_when_missing(self, tpl_file):
        from memory.rooms import get_template
        assert get_template("nope") is None


class TestDeleteTemplate:
    def test_deletes_existing(self, tpl_file):
        from memory.rooms import save_template, delete_template, load_templates
        save_template("temp", "x")
        assert delete_template("temp") is True
        assert load_templates() == []

    def test_returns_false_when_missing(self, tpl_file):
        from memory.rooms import delete_template
        assert delete_template("ghost") is False

    def test_deletes_only_named_template(self, tpl_file):
        from memory.rooms import save_template, delete_template, load_templates
        save_template("keep", "1")
        save_template("drop", "2")
        assert delete_template("drop") is True
        assert load_templates() == [{"name": "keep", "prompt": "1"}]
