"""Tests for /model <name> validation in handle_slash_command."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock


def _make_agent(model="ollama/test:latest"):
    from agent import Agent
    a = Agent({"model": model})
    return a


def _run_model_cmd(arg, available_models, monkeypatch):
    """Run /model <arg>, return ([(kind, msg), ...], agent)."""
    import main
    import memory.rooms as rooms

    captured = []
    monkeypatch.setattr(main, "print_error", lambda m: captured.append(("error", m)))
    monkeypatch.setattr(main, "print_info", lambda m: captured.append(("info", m)))
    monkeypatch.setattr(rooms, "save_meta", lambda *a, **kw: None)
    monkeypatch.setattr(main, "save_default_model", lambda *a: None)
    monkeypatch.setattr(main, "_ollama_model_ids", lambda url: available_models)

    agent = _make_agent()
    state = {"room": "testroom"}
    main.handle_slash_command(f"/model {arg}", agent, state)
    return captured, agent


class TestModelSwitchValidation:
    def test_valid_ollama_model_switches(self, monkeypatch):
        msgs, agent = _run_model_cmd("ollama/llama3:8b", {"ollama/llama3:8b"}, monkeypatch)
        assert any(k == "info" for k, _ in msgs)
        assert agent.model == "ollama/llama3:8b"

    def test_invalid_ollama_model_shows_error(self, monkeypatch):
        msgs, agent = _run_model_cmd("ollama/nonexistent:latest", {"ollama/llama3:8b"}, monkeypatch)
        assert any(k == "error" for k, _ in msgs)
        assert agent.model != "ollama/nonexistent:latest"

    def test_ollama_unreachable_allows_switch(self, monkeypatch):
        """If Ollama is down we can't validate; let the switch through."""
        import main
        import memory.rooms as rooms

        monkeypatch.setattr(main, "print_error", lambda m: None)
        monkeypatch.setattr(main, "print_info", lambda m: None)
        monkeypatch.setattr(rooms, "save_meta", lambda *a, **kw: None)
        monkeypatch.setattr(main, "save_default_model", lambda *a: None)
        monkeypatch.setattr(main, "_ollama_model_ids", lambda url: (_ for _ in ()).throw(Exception("timeout")))

        agent = _make_agent()
        main.handle_slash_command("/model ollama/some:model", agent, {"room": "r"})
        assert agent.model == "ollama/some:model"

    def test_cloud_model_skips_ollama_check(self, monkeypatch):
        msgs, agent = _run_model_cmd("anthropic/claude-3-opus", set(), monkeypatch)
        assert agent.model == "anthropic/claude-3-opus"
        assert not any(k == "error" for k, _ in msgs)

    def test_empty_ollama_list_allows_switch(self, monkeypatch):
        """Empty set = Ollama returned nothing — don't block the switch."""
        msgs, agent = _run_model_cmd("ollama/llama3:8b", set(), monkeypatch)
        assert agent.model == "ollama/llama3:8b"
