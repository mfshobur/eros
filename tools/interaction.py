"""Agent-initiated clarification: lets the model ask the user a question."""
from tools.base import BaseTool, register_tool

_ask_callback = None


def set_ask_callback(fn) -> None:
    """Register the UI handler for ask_user. fn(question, options) -> str."""
    global _ask_callback
    _ask_callback = fn


def request_user_input(question: str, options: list | None = None) -> str:
    if _ask_callback:
        return _ask_callback(question, options)
    return "No interactive user available. Make a reasonable assumption and proceed."


@register_tool
class AskUser(BaseTool):
    name = "ask_user"
    description = (
        "Ask the user a clarifying question when their request is genuinely "
        "ambiguous and you cannot proceed safely. Provide 'options' when the "
        "answer is a choice. Do NOT use this for minor ambiguities you can "
        "resolve with a reasonable assumption."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The clarifying question"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of choices for the user",
            },
        },
        "required": ["question"],
    }

    def execute(self, question: str, options: list | None = None) -> str:
        answer = request_user_input(question, options)
        return f"User answered: {answer}"
