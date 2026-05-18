"""Tests for the constrained tool-calling reliability layer."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import pytest

from tools.reliability import (
    ReliabilityMetrics,
    build_constrained_schema,
    looks_like_tool_narration,
    parse_constrained_response,
    validate_tool_args,
)


def _schemas(*names):
    return [{"type": "function", "function": {"name": n, "parameters": {}}} for n in names]


class TestBuildConstrainedSchema:
    def test_enum_has_every_tool_plus_none(self):
        schema = build_constrained_schema(_schemas("bash", "read_file"))
        assert schema["properties"]["tool"]["enum"] == ["bash", "read_file", "none"]

    def test_required_is_tool_only(self):
        schema = build_constrained_schema(_schemas("bash"))
        assert schema["required"] == ["tool"]
        assert set(schema["properties"]) == {"tool", "arguments", "message"}

    def test_empty_tool_list(self):
        schema = build_constrained_schema([])
        assert schema["properties"]["tool"]["enum"] == ["none"]


class TestParseConstrainedResponse:
    def test_none_returns_message(self):
        calls, msg = parse_constrained_response(
            '{"tool":"none","message":"hello there"}', {"bash"}
        )
        assert calls == []
        assert msg == "hello there"

    def test_tool_call_parsed(self):
        calls, msg = parse_constrained_response(
            '{"tool":"bash","arguments":{"command":"ls"}}', {"bash"}
        )
        assert msg is None
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "bash"
        assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls"}

    def test_json_embedded_in_prose(self):
        calls, msg = parse_constrained_response(
            'Here is my decision: {"tool":"bash","arguments":{"command":"pwd"}} done',
            {"bash"},
        )
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "bash"

    def test_unknown_tool_falls_back_to_message(self):
        calls, msg = parse_constrained_response(
            '{"tool":"frobnicate","message":"oops"}', {"bash"}
        )
        assert calls == []
        assert msg == "oops"

    def test_garbage_returns_empty(self):
        calls, msg = parse_constrained_response("not json at all", {"bash"})
        assert calls == []
        assert msg is None


class TestValidateToolArgs:
    def setup_method(self):
        from tools.base import load_tools
        load_tools(["file_ops", "bash"])

    @property
    def _registry(self):
        from tools.base import get_all_tools
        return get_all_tools()

    def test_missing_required_arg(self):
        err = validate_tool_args("edit_file", {"path": "a.py"}, self._registry)
        assert err is not None
        assert "old_string" in err
        assert "path" in err  # lists what was provided

    def test_all_required_present(self):
        err = validate_tool_args(
            "write_file", {"path": "a.py", "content": "x"}, self._registry
        )
        assert err is None

    def test_wrong_type(self):
        err = validate_tool_args(
            "bash", {"command": "ls", "timeout": "not-a-number"}, self._registry
        )
        assert err is not None
        assert "timeout" in err

    def test_numeric_string_tolerated(self):
        err = validate_tool_args(
            "bash", {"command": "ls", "timeout": "30"}, self._registry
        )
        assert err is None

    def test_unknown_tool_defers(self):
        err = validate_tool_args("nonexistent", {"x": 1}, self._registry)
        assert err is None

    def test_extra_args_tolerated(self):
        err = validate_tool_args(
            "bash", {"command": "ls", "extra_thing": "whatever"}, self._registry
        )
        assert err is None


class TestLooksLikeToolNarration:
    KNOWN = {"bash", "read_file", "write_file"}

    def test_verb_plus_tool_word(self):
        assert looks_like_tool_narration("I'll run bash to list files", self.KNOWN)

    def test_bracket_narration(self):
        assert looks_like_tool_narration("[called bash: echo hi]", self.KNOWN)

    def test_lone_fenced_shell_block(self):
        assert looks_like_tool_narration("```bash\nls -la\n```", self.KNOWN)

    def test_malformed_json_naming_tool(self):
        assert looks_like_tool_narration('{"name": "bash"', self.KNOWN)

    def test_plain_greeting_is_not_narration(self):
        assert not looks_like_tool_narration("Hello, how can I help?", self.KNOWN)

    def test_long_genuine_answer_not_narration(self):
        text = "Here is a detailed explanation. " * 60  # >1200 chars, no cue
        assert not looks_like_tool_narration(text, self.KNOWN)

    def test_empty(self):
        assert not looks_like_tool_narration("", self.KNOWN)


class TestReliabilityMetrics:
    def test_fresh_is_all_zero(self):
        m = ReliabilityMetrics()
        assert m.model_turns == 0
        assert m.constrained_retry_uses == 0

    def test_summary_rows_zero_turns_rate(self):
        m = ReliabilityMetrics()
        rows = dict(m.summary_rows())
        assert rows["First-pass success rate"] == "—"

    def test_summary_rows_computes_rate(self):
        m = ReliabilityMetrics(
            model_turns=4, first_pass_tool_calls=3, parser_fallback_uses=1
        )
        rows = dict(m.summary_rows())
        assert rows["First-pass success rate"] == "75%"
        assert rows["Model turns"] == "4"
