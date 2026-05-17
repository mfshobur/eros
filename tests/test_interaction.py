"""Tests for agent-initiated clarification (ask_user tool)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture(autouse=True)
def reset_callback():
    import tools.interaction as interaction
    interaction._ask_callback = None
    yield
    interaction._ask_callback = None


class TestAskUser:
    def test_returns_callback_answer(self):
        from tools.interaction import AskUser, set_ask_callback
        set_ask_callback(lambda q, o: "blue")
        tool = AskUser()
        result = tool.execute(question="What color?")
        assert "blue" in result

    def test_default_when_no_callback(self):
        from tools.interaction import AskUser
        tool = AskUser()
        result = tool.execute(question="anything?")
        assert "No interactive user available" in result

    def test_schema_format(self):
        from tools.interaction import AskUser
        schema = AskUser().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "ask_user"
        assert "question" in schema["function"]["parameters"]["properties"]

    def test_passes_options_to_callback(self):
        from tools.interaction import AskUser, set_ask_callback
        received = {}

        def cb(question, options):
            received["question"] = question
            received["options"] = options
            return options[0]

        set_ask_callback(cb)
        AskUser().execute(question="Pick one", options=["a", "b"])
        assert received["question"] == "Pick one"
        assert received["options"] == ["a", "b"]

    def test_set_ask_callback_overrides(self):
        from tools.interaction import AskUser, set_ask_callback
        set_ask_callback(lambda q, o: "first")
        set_ask_callback(lambda q, o: "second")
        result = AskUser().execute(question="?")
        assert "second" in result


class TestAskUserRegistration:
    def test_registered_in_registry(self):
        import tools.interaction  # noqa: F401  (triggers @register_tool)
        from tools.base import _REGISTRY
        assert "ask_user" in _REGISTRY

    def test_exposed_in_schemas_despite_groups(self):
        """ask_user is not in any group but must still reach the model."""
        import tools.interaction  # noqa: F401
        from tools.base import get_tool_schemas
        schemas = get_tool_schemas(["file_ops", "bash"])
        names = [s["function"]["name"] for s in schemas]
        assert "ask_user" in names


class TestLooksLikeQuestion:
    def test_detects_clarifying_question(self):
        from agent import _looks_like_question
        assert _looks_like_question("Which file should I delete?")
        assert _looks_like_question("Could you please specify which file has the bug?")

    def test_detects_imperative_clarification(self):
        from agent import _looks_like_question
        assert _looks_like_question(
            "I cannot fix the bug without knowing which file. Please provide the file path."
        )
        assert _looks_like_question("I need to know which file you mean.")

    def test_rejects_statement(self):
        from agent import _looks_like_question
        assert not _looks_like_question("The answer is 4.")
        assert not _looks_like_question("Done. The file was deleted.")

    def test_rejects_rhetorical_without_cue(self):
        from agent import _looks_like_question
        assert not _looks_like_question("Make sense?")
        assert not _looks_like_question("Got it?")

    def test_rejects_long_text(self):
        from agent import _looks_like_question
        long = "This is a long explanation of how things work. " * 10 + "which one?"
        assert not _looks_like_question(long)

    def test_rejects_multiparagraph(self):
        from agent import _looks_like_question
        assert not _looks_like_question("Here is the answer.\n\nWhich file should I use?")


class TestClarificationLoop:
    def test_plain_text_question_routed_to_ask_user(self):
        """A plain-text clarifying question is converted into an ask_user call
        so the loop pauses for the user and then continues."""
        from agent import Agent
        from config import load_config
        from tools.base import load_tools
        from tools.interaction import set_ask_callback

        load_tools(["file_ops"])  # ensures ask_user is registered
        asked = []
        set_ask_callback(lambda q, o: asked.append(q) or "report.txt")

        agent = Agent(load_config())
        responses = [
            ("Which file should I delete?", "", [], {}),
            ("Deleted report.txt.", "", [], {}),
        ]
        agent._stream_response = lambda *a, **k: responses.pop(0)
        final, _, _ = agent._run_loop([], [], None, None, None, None)

        assert asked == ["Which file should I delete?"]
        assert "report.txt" in final

    def test_statement_response_not_intercepted(self):
        from agent import Agent
        from config import load_config
        from tools.base import load_tools
        from tools.interaction import set_ask_callback

        load_tools(["file_ops"])
        asked = []
        set_ask_callback(lambda q, o: asked.append(q) or "x")

        agent = Agent(load_config())
        agent._stream_response = lambda *a, **k: ("All done.", "", [], {})
        final, _, _ = agent._run_loop([], [], None, None, None, None)

        assert asked == []
        assert final == "All done."
