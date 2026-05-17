"""Tests for permission management: allowlist rules + request_permission."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture
def perm_file(tmp_path, monkeypatch):
    import tools.permissions as perms
    f = tmp_path / "permissions.json"
    monkeypatch.setattr(perms, "_PERMISSIONS_FILE", f)
    return f


@pytest.fixture(autouse=True)
def reset_permission_state():
    import tools.base as base
    base._permission_mode = "auto"
    base._permission_callback = None
    base._pending_note = ""
    yield
    base._permission_mode = "auto"
    base._permission_callback = None
    base._pending_note = ""


# ── command_prefix ────────────────────────────────────────────────────────────

class TestCommandPrefix:
    def test_command_with_subcommand(self):
        from tools.permissions import command_prefix
        assert command_prefix("git checkout -b feature") == "git checkout"
        assert command_prefix("npm install lodash") == "npm install"

    def test_flag_second_token_uses_first(self):
        from tools.permissions import command_prefix
        assert command_prefix("ls -la") == "ls"

    def test_single_token(self):
        from tools.permissions import command_prefix
        assert command_prefix("pwd") == "pwd"

    def test_empty(self):
        from tools.permissions import command_prefix
        assert command_prefix("") == ""


# ── add_rule / matches ────────────────────────────────────────────────────────

class TestRules:
    def test_bash_rule_matches_same_prefix(self, perm_file):
        from tools.permissions import add_rule, matches
        add_rule("/proj", "bash", {"command": "git checkout -b x"})
        assert matches("/proj", "bash", {"command": "git checkout main"})

    def test_bash_rule_rejects_other_prefix(self, perm_file):
        from tools.permissions import add_rule, matches
        add_rule("/proj", "bash", {"command": "git checkout -b x"})
        assert not matches("/proj", "bash", {"command": "git push"})

    def test_rule_scoped_to_directory(self, perm_file):
        from tools.permissions import add_rule, matches
        add_rule("/proj-a", "bash", {"command": "git status"})
        assert not matches("/proj-b", "bash", {"command": "git status"})

    def test_file_tool_rule_matches_any_args(self, perm_file):
        from tools.permissions import add_rule, matches
        add_rule("/proj", "write_file", {"path": "a.txt"})
        assert matches("/proj", "write_file", {"path": "totally-different.txt"})

    def test_persists_and_reloads(self, perm_file):
        from tools.permissions import add_rule, load_rules
        add_rule("/proj", "bash", {"command": "git pull"})
        assert perm_file.exists()
        assert load_rules()["/proj"] == [{"tool": "bash", "prefix": "git pull"}]

    def test_no_duplicate_rules(self, perm_file):
        from tools.permissions import add_rule, load_rules
        add_rule("/proj", "bash", {"command": "git pull origin"})
        add_rule("/proj", "bash", {"command": "git pull upstream"})
        assert len(load_rules()["/proj"]) == 1


# ── request_permission ────────────────────────────────────────────────────────

class TestRequestPermission:
    def test_auto_mode_non_dangerous_allows(self, perm_file):
        from tools.base import request_permission, set_permission_mode
        set_permission_mode("auto")
        assert request_permission("bash", {"command": "ls"}, "ls") is True

    def test_manual_allowlisted_skips_callback(self, perm_file):
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        from tools.permissions import add_rule
        set_permission_mode("manual")
        called = []
        set_permission_callback(lambda *a: called.append(1) or PermissionDecision("deny", ""))
        add_rule(os.getcwd(), "bash", {"command": "git status"})
        assert request_permission("bash", {"command": "git status -s"}, "git status -s") is True
        assert called == []

    def test_manual_unlisted_invokes_callback(self, perm_file):
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        set_permission_mode("manual")
        set_permission_callback(lambda *a: PermissionDecision("once", ""))
        assert request_permission("bash", {"command": "whoami"}, "whoami") is True

    def test_deny(self, perm_file):
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        set_permission_mode("manual")
        set_permission_callback(lambda *a: PermissionDecision("deny", ""))
        assert request_permission("bash", {"command": "whoami"}, "whoami") is False

    def test_always_adds_rule(self, perm_file):
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        from tools.permissions import matches
        set_permission_mode("manual")
        set_permission_callback(lambda *a: PermissionDecision("always", ""))
        assert request_permission("bash", {"command": "git diff HEAD"}, "git diff HEAD") is True
        assert matches(os.getcwd(), "bash", {"command": "git diff --stat"})

    def test_dangerous_no_callback_blocked(self, perm_file):
        from tools.base import request_permission, set_permission_mode
        set_permission_mode("auto")
        assert request_permission("bash", {"command": "rm x"}, "rm x", dangerous=True) is False

    def test_non_dangerous_no_callback_allowed(self, perm_file):
        from tools.base import request_permission, set_permission_mode
        set_permission_mode("manual")
        assert request_permission("bash", {"command": "ls"}, "ls") is True

    def test_dangerous_cannot_be_allowlisted(self, perm_file):
        from tools.base import request_permission, set_permission_mode, set_permission_callback, PermissionDecision
        from tools.permissions import matches
        set_permission_mode("manual")
        set_permission_callback(lambda *a: PermissionDecision("always", ""))
        request_permission("bash", {"command": "rm file"}, "rm file", dangerous=True)
        assert not matches(os.getcwd(), "bash", {"command": "rm file"})

    def test_pending_note_set_and_consumed(self, perm_file):
        from tools.base import (request_permission, set_permission_mode,
                                set_permission_callback, consume_permission_note, PermissionDecision)
        set_permission_mode("manual")
        set_permission_callback(lambda *a: PermissionDecision("deny", "do not touch that"))
        request_permission("bash", {"command": "whoami"}, "whoami")
        assert consume_permission_note() == "do not touch that"
        assert consume_permission_note() == ""


# ── pick_permission selector (scripted keystrokes) ────────────────────────────

class TestPickPermission:
    @staticmethod
    def _keys(seq):
        it = iter(seq)
        return lambda: next(it)

    def test_enter_selects_yes(self, monkeypatch):
        import ui.picker as picker
        monkeypatch.setattr(picker, "_read_key", self._keys(["\r"]))
        assert picker.pick_permission(False, "git status") == ("once", "")

    def test_down_then_enter_selects_always(self, monkeypatch):
        import ui.picker as picker
        monkeypatch.setattr(picker, "_read_key", self._keys(["down", "\r"]))
        action, _ = picker.pick_permission(False, "git status")
        assert action == "always"

    def test_esc_denies(self, monkeypatch):
        import ui.picker as picker
        monkeypatch.setattr(picker, "_read_key", self._keys(["esc"]))
        action, _ = picker.pick_permission(False, "git status")
        assert action == "deny"

    def test_tab_adds_note(self, monkeypatch):
        import ui.picker as picker
        monkeypatch.setattr(picker, "_read_key", self._keys(["tab", "n", "o", "p", "e", "\r"]))
        action, note = picker.pick_permission(False, "git status")
        assert action == "once"
        assert note == "nope"

    def test_dangerous_has_no_always_option(self, monkeypatch):
        import ui.picker as picker
        # only 2 options when dangerous: down from Yes lands on No
        monkeypatch.setattr(picker, "_read_key", self._keys(["down", "\r"]))
        action, _ = picker.pick_permission(True, "rm")
        assert action == "deny"
