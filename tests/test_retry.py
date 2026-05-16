"""Tests for /retry command."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch


def _make_agent_with_history():
    agent = MagicMock()
    agent.config = {}
    agent.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    return agent


class TestRetryCommand:
    def test_retry_with_no_last_turn_prints_error(self, capsys):
        from main import handle_slash_command
        agent = MagicMock()
        agent.config = {}
        state = {"room": "test"}
        handle_slash_command("/retry", agent, state)
        out = capsys.readouterr().out
        assert "Nothing to retry" in out

    def test_retry_pops_last_two_history_entries(self):
        from main import handle_slash_command
        agent = _make_agent_with_history()
        state = {
            "room": "test",
            "last_turn": {
                "expanded": "hello",
                "original_input": "hello",
                "raw_input": "hello",
                "images": [],
            },
        }
        with patch("main._run_chat") as mock_run:
            handle_slash_command("/retry", agent, state)
        assert agent.history == []
        mock_run.assert_called_once()

    def test_retry_passes_last_turn_inputs(self):
        from main import handle_slash_command
        agent = _make_agent_with_history()
        last = {
            "expanded": "what is 2+2?",
            "original_input": "what is 2+2?",
            "raw_input": "what is 2+2?",
            "images": [],
        }
        state = {"room": "test", "last_turn": last}
        with patch("main._run_chat") as mock_run:
            handle_slash_command("/retry", agent, state)
        call_kwargs = mock_run.call_args
        assert "what is 2+2?" in str(call_kwargs)
