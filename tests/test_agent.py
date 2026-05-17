"""Tests for agent complexity detection and tool dispatch."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import re
import pytest
from agent import Agent, _claims_file_change


# ── _looks_complex ──────────────────────────────────────────────────────────

_COMPLEX_PATTERNS = re.compile(
    r"\b(loop|repeat|iterate|for each|step\s*\d|multi.?step|compute|calculate|parse|process|count|total|sum|compare|report)\b",
    re.IGNORECASE,
)
_EDIT_PATTERNS = re.compile(
    r"\b(change|replace|update|rename|modify|correct|fix)\b.{0,120}\b(in|inside|within|to|from)\b",
    re.IGNORECASE,
)


def looks_complex(text: str) -> bool:
    if _EDIT_PATTERNS.search(text):
        return True
    return bool(_COMPLEX_PATTERNS.search(text)) and len(text) > 100


class TestLooksComplex:
    def test_edit_pattern_change_in(self):
        assert looks_complex("change the model name in config.yaml")

    def test_edit_pattern_replace_with(self):
        assert looks_complex("replace foo with bar in main.py")

    def test_edit_pattern_fix_in(self):
        assert looks_complex("fix the typo in README.md")

    def test_complex_pattern_with_long_text(self):
        long = "please process all the lines and count the total occurrences of each word " + "x" * 60
        assert looks_complex(long)

    def test_complex_pattern_short_text_not_flagged(self):
        # short text without edit pattern → not complex
        assert not looks_complex("count items")

    def test_simple_question_not_flagged(self):
        assert not looks_complex("what is the capital of France?")

    def test_greeting_not_flagged(self):
        assert not looks_complex("hello")

    def test_file_content_with_trigger_words_not_flagged(self):
        # raw input before @file expansion; just the reference, not the file body
        assert not looks_complex("@data.csv")


# ── _claims_file_change ─────────────────────────────────────────────────────

class TestClaimsFileChange:
    def test_has_been_updated(self):
        assert _claims_file_change("The file has been updated successfully.")

    def test_saved_to_file(self):
        assert _claims_file_change("I saved to file config.yaml.")

    def test_plain_response_not_flagged(self):
        assert not _claims_file_change("Here is the content you asked for.")

    def test_now_contains(self):
        assert _claims_file_change("The file now contains the new value.")


# ── dispatch_tool ────────────────────────────────────────────────────────────

class TestDispatchTool:
    def setup_method(self):
        from tools.base import load_tools
        load_tools(["file_ops", "bash"])

    def test_unknown_tool_returns_error(self):
        from tools.base import dispatch_tool
        result = dispatch_tool("nonexistent_tool", {})
        assert "unknown tool" in result

    def test_path_annotation_sanitized(self):
        """dispatch_tool strips annotation noise like '[file: "foo.txt"]' from path args."""
        from tools.base import dispatch_tool
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello")
            tmp = f.name
        try:
            result = dispatch_tool("read_file", {"path": f'[file: "{tmp}"]'})
            assert "hello" in result
        finally:
            os.unlink(tmp)


# ── permission system ────────────────────────────────────────────────────────

class TestPermissions:
    def teardown_method(self):
        from tools.base import set_permission_mode, set_permission_callback
        set_permission_mode("auto")
        set_permission_callback(None)

    def test_auto_mode_allows_all(self):
        from tools.base import request_permission, set_permission_mode
        set_permission_mode("auto")
        assert request_permission("bash", {"command": "ls"}, "ls") is True

    def test_manual_mode_no_callback_allows(self):
        from tools.base import request_permission, set_permission_mode, set_permission_callback
        set_permission_mode("manual")
        set_permission_callback(None)
        assert request_permission("bash", {"command": "ls"}, "ls") is True

    def test_manual_mode_callback_deny(self, monkeypatch, tmp_path):
        import tools.permissions as perms
        monkeypatch.setattr(perms, "_PERMISSIONS_FILE", tmp_path / "p.json")
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        set_permission_mode("manual")
        set_permission_callback(lambda name, args, preview, dangerous: PermissionDecision("deny", ""))
        assert request_permission("bash", {"command": "ls"}, "ls") is False

    def test_manual_mode_callback_allow(self, monkeypatch, tmp_path):
        import tools.permissions as perms
        monkeypatch.setattr(perms, "_PERMISSIONS_FILE", tmp_path / "p.json")
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        set_permission_mode("manual")
        set_permission_callback(lambda name, args, preview, dangerous: PermissionDecision("once", ""))
        assert request_permission("bash", {"command": "ls"}, "ls") is True

    def test_get_set_permission_mode(self):
        from tools.base import get_permission_mode, set_permission_mode
        set_permission_mode("manual")
        assert get_permission_mode() == "manual"
        set_permission_mode("auto")
        assert get_permission_mode() == "auto"
