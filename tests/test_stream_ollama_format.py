"""Tests for the Ollama `format` (constrained generation) path in _stream_ollama."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import pytest

from agent import Agent


class _FakeResponse:
    """Context-manager iterable mimicking urllib's streaming response."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def _ndjson(*objs) -> list[bytes]:
    return [(json.dumps(o) + "\n").encode() for o in objs]


def _agent():
    return Agent({"model": "ollama/test", "ollama_base_url": "http://x"})


class TestFormatParam:
    def test_format_sent_when_response_format_given(self, monkeypatch):
        captured = {}

        def fake_urlopen(req):
            captured["body"] = json.loads(req.data)
            return _FakeResponse(_ndjson(
                {"message": {"content": '{"tool":"none","message":"hi"}'}},
                {"done": True, "prompt_eval_count": 1, "eval_count": 2},
            ))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        schema = {"type": "object", "properties": {"tool": {"type": "string"}}}
        _agent()._stream_ollama([], None, None, None, response_format=schema)
        assert captured["body"]["format"] == schema

    def test_no_format_key_when_omitted(self, monkeypatch):
        captured = {}

        def fake_urlopen(req):
            captured["body"] = json.loads(req.data)
            return _FakeResponse(_ndjson(
                {"message": {"content": "plain answer"}},
                {"done": True},
            ))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        _agent()._stream_ollama([], None, None, None)
        assert "format" not in captured["body"]


class TestConstrainedParsing:
    def test_none_response_returns_message_no_raw_json_streamed(self, monkeypatch):
        def fake_urlopen(req):
            return _FakeResponse(_ndjson(
                {"message": {"content": '{"tool":"none",'}},
                {"message": {"content": '"message":"the answer"}'}},
                {"done": True},
            ))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        streamed = []
        text, thinking, calls, usage = _agent()._stream_ollama(
            [], None, streamed.append, None,
            response_format={"type": "object"},
        )
        assert text == "the answer"
        assert calls == []
        # raw JSON must NOT have been echoed token-by-token
        assert "".join(streamed) == "the answer"
        assert '{"tool"' not in "".join(streamed)

    def test_tool_call_response_parsed(self, monkeypatch):
        def fake_urlopen(req):
            return _FakeResponse(_ndjson(
                {"message": {"content": '{"tool":"bash","arguments":{"command":"ls"}}'}},
                {"done": True},
            ))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        # bash must be a known tool for parse_constrained_response
        from tools.base import load_tools
        load_tools(["bash"])
        text, thinking, calls, usage = _agent()._stream_ollama(
            [], None, None, None, response_format={"type": "object"},
        )
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "bash"
        assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls"}
