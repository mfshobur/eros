"""Constrained tool-calling reliability layer.

Small Ollama models (3b-14b) frequently fail at tool calling: broken JSON,
prose-narrated calls (`[called bash: ls]`), missing required arguments. This
module provides the pieces that *prevent* and *validate* tool calls rather
than only salvaging them after the fact:

- ``build_constrained_schema`` — a JSON Schema for Ollama's ``format`` param
  that grammar-constrains a regenerated turn to a parseable shape.
- ``parse_constrained_response`` — reads back a constrained response.
- ``validate_tool_args`` — checks parsed args against a tool's own schema.
- ``looks_like_tool_narration`` — detects a tool call narrated as prose.
- ``ReliabilityMetrics`` — per-session counters surfaced by ``/reliability``.
"""
import json
import re
from dataclasses import dataclass


# --- Part 1: schema-constrained regeneration -------------------------------

def build_constrained_schema(tools: list[dict]) -> dict:
    """Build a flat union JSON Schema for Ollama's ``format`` parameter.

    ``tools`` is the output of ``get_tool_schemas()``. The schema lets the
    model emit *either* a tool call *or* a plain-text answer (``tool="none"``).
    ``arguments`` is left a loose object on purpose: a deep per-tool ``oneOf``
    is slow and unreliable for small-model grammar decoding — per-tool
    argument correctness is enforced separately by ``validate_tool_args``.
    """
    names = [t["function"]["name"] for t in tools if t.get("function", {}).get("name")]
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": names + ["none"]},
            "arguments": {"type": "object"},
            "message": {"type": "string"},
        },
        "required": ["tool"],
    }


def _first_json_object(text: str) -> str | None:
    """Extract the first complete ``{...}`` object from text (depth-counting).

    Mirrors the extraction logic in ``agent._parse_text_tool_call`` so a JSON
    object embedded in prose / reasoning is still recovered.
    """
    brace = text.find("{")
    if brace == -1:
        return None
    text = text[brace:]
    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(text):
        if escape:
            escape = False
        elif ch == "\\" and in_str:
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[: i + 1]
    return None


def parse_constrained_response(text: str, known_tools: set[str]) -> tuple[list[dict], str | None]:
    """Parse a JSON object produced under ``build_constrained_schema``.

    Returns ``(tool_calls, final_message)``:
      - ``tool == "none"``  -> ``([], message)``        a final text answer
      - ``tool == <name>``  -> ``([call], None)``       dispatch the tool
      - unparseable / unknown tool -> ``([], None)``    caller falls back
    The call dict matches the shape ``_run_loop`` expects from the parsers.
    """
    raw = _first_json_object(text)
    if raw is None:
        return [], None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [], None
    if not isinstance(obj, dict):
        return [], None

    tool = obj.get("tool")
    message = obj.get("message")
    message = message if isinstance(message, str) else None

    if tool == "none" or not tool or not isinstance(tool, str):
        return [], message
    if tool not in known_tools:
        # model named a tool that does not exist — treat as a text answer
        return [], message

    args = obj.get("arguments")
    if not isinstance(args, dict):
        args = {}
    call = {
        "id": "constrained_call_0",
        "type": "function",
        "function": {"name": tool, "arguments": json.dumps(args)},
    }
    return [call], None


# --- Part 2: argument validation -------------------------------------------

# JSON Schema type name -> accepted Python type(s).
_TYPE_MAP: dict[str, tuple] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _looks_numeric(value) -> bool:
    """True if a string value is really a number (small models stringify ints)."""
    if not isinstance(value, str):
        return False
    try:
        float(value)
        return True
    except ValueError:
        return False


def validate_tool_args(tool_name: str, args: dict, registry: dict) -> str | None:
    """Validate parsed args against a tool's ``parameters`` JSON Schema.

    Returns ``None`` if valid, otherwise a single precise, model-actionable
    error string. Unknown tools return ``None`` (``dispatch_tool`` owns that
    error). Only the first problem is reported — one instruction at a time
    works best for small models.
    """
    tool = registry.get(tool_name)
    if tool is None:
        return None
    schema = getattr(tool, "parameters", None) or {}
    props: dict = schema.get("properties", {}) or {}
    required: list = schema.get("required", []) or []

    for r in required:
        if r not in args:
            return (
                f"Tool '{tool_name}' is missing required argument '{r}'. "
                f"Provided: {sorted(args)}. "
                f"Call it again with all required arguments: {list(required)}."
            )

    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue  # extra args not in the schema are tolerated
        expected = spec.get("type")
        accepted = _TYPE_MAP.get(expected)
        if accepted and not isinstance(value, accepted):
            # tolerate numeric strings for numeric fields
            if expected in ("integer", "number") and _looks_numeric(value):
                pass
            # bool is an int subclass; reject an int passed where bool expected
            elif expected == "boolean" and isinstance(value, bool):
                pass
            else:
                got = type(value).__name__
                return (
                    f"Tool '{tool_name}' argument '{key}' must be of type "
                    f"'{expected}', got {got}. Call it again with '{key}' as a {expected}."
                )
        enum = spec.get("enum")
        if isinstance(enum, list) and value not in enum:
            return (
                f"Tool '{tool_name}' argument '{key}' must be one of {enum}, "
                f"got {value!r}. Call it again with a valid value."
            )
    return None


# --- Part 3: plain-text tool-call narration --------------------------------

# "[called bash: ls]", "[running tool read_file]", "[invoking web_search]"
_NARRATION_BRACKET = re.compile(
    r"\[\s*(called|calling|running|using|invoking|tool|executing)\b[^\]]*\]",
    re.IGNORECASE,
)
# A lone fenced shell block — the model pasted a command instead of calling bash.
_NARRATION_FENCE = re.compile(r"```(?:bash|sh|shell|console)\b", re.IGNORECASE)
# "I'll run", "let me read", "I am going to execute", "going to call" ...
_NARRATION_VERB = re.compile(
    r"\b(i'?ll|i will|let me|i'?m going to|i am going to|now i'?ll|"
    r"i need to|going to|let'?s)\s+"
    r"(run|execute|call|use|read|open|search|fetch|list|write|edit|append|check)\b",
    re.IGNORECASE,
)


def looks_like_tool_narration(text: str, known_tools: set[str]) -> bool:
    """True if the response narrates a tool call as prose instead of emitting it.

    Called after no real tool call was parsed, to decide whether to regenerate
    the turn under a schema constraint. Mirrors the ``_CLARIFY_CUE`` approach
    in ``agent.py``.
    """
    t = (text or "").strip()
    if not t:
        return False

    bracket_or_fence = bool(_NARRATION_BRACKET.search(t)) or bool(_NARRATION_FENCE.search(t))
    if bracket_or_fence and (len(t) < 600 or _NARRATION_BRACKET.search(t)):
        return True

    # Beyond this point, a long answer that merely mentions a tool word is a
    # genuine reply, not a narrated call — don't fire.
    if len(t) > 1200:
        return False

    mentions_tool = any(
        re.search(rf"\b{re.escape(name)}\b", t) for name in known_tools
    )
    if mentions_tool and _NARRATION_VERB.search(t):
        return True
    # An attempted-but-malformed JSON tool call: has a brace and names a tool.
    if "{" in t and mentions_tool:
        return True
    return False


# --- Part 4: session metrics -----------------------------------------------

@dataclass
class ReliabilityMetrics:
    """Per-session tool-call reliability counters (one per Agent instance)."""

    model_turns: int = 0                # total model generations in _run_loop
    first_pass_tool_calls: int = 0      # tool call parsed on the free-form attempt
    parser_fallback_uses: int = 0       # heredoc/salvage parser produced the call
    constrained_retry_uses: int = 0     # a turn was regenerated with `format`
    constrained_retry_success: int = 0  # the regeneration yielded a parseable result
    arg_validation_failures: int = 0    # validate_tool_args rejected a call
    plaintext_narration_caught: int = 0  # the narration detector fired

    def summary_rows(self) -> list[tuple[str, str]]:
        """Pre-formatted ``(label, value)`` rows for the /reliability panel."""
        obtained = (
            self.first_pass_tool_calls
            + self.parser_fallback_uses
            + self.constrained_retry_success
        )
        if obtained:
            rate = f"{100 * self.first_pass_tool_calls / obtained:.0f}%"
        else:
            rate = "—"
        return [
            ("Model turns", str(self.model_turns)),
            ("First-pass tool calls", str(self.first_pass_tool_calls)),
            ("First-pass success rate", rate),
            ("Parser-fallback uses", str(self.parser_fallback_uses)),
            ("Constrained retries", str(self.constrained_retry_uses)),
            ("  └ of those, succeeded", str(self.constrained_retry_success)),
            ("Plain-text calls caught", str(self.plaintext_narration_caught)),
            ("Arg-validation failures", str(self.arg_validation_failures)),
        ]
