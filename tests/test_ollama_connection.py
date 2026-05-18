"""Tests for friendly Ollama connection error in _stream_ollama."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import urllib.error
from unittest.mock import patch

from agent import Agent


class TestOllamaConnectionError:
    def _agent(self):
        return Agent({"model": "ollama/test", "ollama_base_url": "http://localhost:11434"})

    def test_url_error_raises_runtime_error(self, monkeypatch):
        def fake_urlopen(req):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        agent = self._agent()
        with pytest.raises(RuntimeError) as exc_info:
            agent._stream_ollama([], None, None, None)
        msg = str(exc_info.value)
        assert "ollama serve" in msg
        assert "localhost:11434" in msg

    def test_error_message_includes_base_url(self, monkeypatch):
        def fake_urlopen(req):
            raise urllib.error.URLError("timeout")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        agent = Agent({"model": "ollama/test", "ollama_base_url": "http://custom:12345"})
        with pytest.raises(RuntimeError) as exc_info:
            agent._stream_ollama([], None, None, None)
        assert "custom:12345" in str(exc_info.value)

    def test_keyboard_interrupt_propagates(self, monkeypatch):
        def fake_urlopen(req):
            raise KeyboardInterrupt

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        agent = self._agent()
        with pytest.raises(KeyboardInterrupt):
            agent._stream_ollama([], None, None, None)
