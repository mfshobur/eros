import base64
import json
import mimetypes
import os
import platform
import re
import subprocess
import sys
import urllib.request
from typing import Callable

from tools.base import get_tool_schemas, dispatch_tool, get_all_tools


def _get_litellm():
    import logging
    import litellm
    from litellm import completion
    litellm.set_verbose = False
    litellm.suppress_debug_info = True
    logging.getLogger("litellm").setLevel(logging.CRITICAL)
    return litellm, completion


_litellm_cache: tuple | None = None


def _litellm():
    global _litellm_cache
    if _litellm_cache is None:
        _litellm_cache = _get_litellm()
    return _litellm_cache

_NATIVE_TOOL_CALL_PREFIXES = ("anthropic/", "openai/", "groq/", "cohere/", "mistral/")


_FILE_CHANGE_CLAIMS = re.compile(
    r"\b(has been (updated|modified|changed|edited|written|saved)|"
    r"(updated|modified|changed|edited|saved) (the |to |in )?file|"
    r"information has been (added|updated|appended)|"
    r"now (reads|contains|updated))\b",
    re.IGNORECASE,
)
_WRITE_TOOLS = {"write_file", "edit_file", "append_file"}


def _claims_file_change(text: str) -> bool:
    return bool(_FILE_CHANGE_CLAIMS.search(text))


# Interrogative-form clarification ("Which file should I delete?")
_CLARIFY_CUE = re.compile(
    r"\b(which|what|where|who|whom|when|"
    r"should i|shall i|do you want|would you like|could you|can you|"
    r"did you mean|specify|clarify)\b",
    re.IGNORECASE,
)

# Imperative-form clarification ("Please provide the file name.")
_CLARIFY_IMPERATIVE = re.compile(
    r"\b(please (?:provide|specify|tell me|clarify|confirm)|"
    r"(?:could|can) you (?:provide|specify|clarify|tell me)|"
    r"i need (?:to know|more (?:details|information|context))|"
    r"let me know which)\b",
    re.IGNORECASE,
)

_MAX_CLARIFY = 3  # max clarification rounds per turn


def _looks_like_question(text: str) -> bool:
    """True if the response is a short, standalone clarification request.

    Small local models reliably ask for clarification in plain text instead of
    calling the ask_user tool; this lets the agent loop catch that and route it
    through the same clarification flow. Catches both interrogative form
    ("Which file?") and imperative form ("Please provide the file name.").
    """
    t = text.strip()
    if not t or len(t) > 250 or "\n\n" in t:
        return False
    if t.endswith("?") and _CLARIFY_CUE.search(t):
        return True
    return bool(_CLARIFY_IMPERATIVE.search(t))


def _env_context() -> str:
    from datetime import datetime
    system = platform.system()
    release = platform.release()
    machine = platform.machine()
    shell = os.environ.get("SHELL", "unknown").split("/")[-1]
    cwd = os.getcwd()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if system == "Darwin":
        os_name = f"macOS {release} ({machine})"
    elif system == "Linux":
        os_name = f"Linux {release} ({machine})"
    else:
        os_name = f"{system} {release} ({machine})"
    day = datetime.now().strftime("%A")
    return (
        f"\n\nToday is {day}, {now}. "
        f"Environment: OS={os_name}, shell={shell}, cwd={cwd}. "
        f"Use OS-appropriate commands (e.g. on macOS use `du -d1` not `du --max-depth=1`, "
        f"`gstat` instead of `stat` if needed, BSD flags for `ls`/`find`)."
    )

def _load_memories() -> str:
    from memory.rooms import load_memories
    facts = load_memories()
    if not facts:
        return ""
    return "\n\n# Memory\n" + "\n".join(facts)


def _load_project_context() -> str:
    from pathlib import Path
    for name in ("EROS.md", "CLAUDE.md"):
        p = Path(os.getcwd()) / name
        if p.exists():
            try:
                content = p.read_text().strip()
                if content:
                    return f"\n\n# Project context ({name})\n{content}"
            except OSError:
                pass
    return ""


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _encode_image(path: str) -> dict | None:
    """Return a LiteLLM vision content block for a local image file."""
    try:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
    except OSError:
        return None


_TOOL_PROMPT_TEMPLATE = """
You have access to the following tools:

{tool_list}

HOW TO CALL A TOOL: output ONLY this JSON on a single line, nothing else before or after:
{{"name": "tool_name", "arguments": {{"arg1": "value1"}}}}

SPECIAL RULE for write_file and append_file with large or multi-line content:
Instead of embedding content in JSON (which breaks with quotes), use this two-part format:
{{"name": "write_file", "arguments": {{"path": "filename.html"}}}}
STARTCONTENT
paste the file content here, no escaping needed, use real quotes freely
ENDCONTENT

For large files (HTML pages, scripts, etc.), break into multiple append_file calls:
1. write_file with STARTCONTENT...ENDCONTENT for the opening section
2. append_file with STARTCONTENT...ENDCONTENT for each subsequent section
3. append_file with STARTCONTENT...ENDCONTENT for the closing tags

NEVER paste file content directly in chat. ALWAYS use write_file or append_file to save it to disk.

FOR COMPLEX TASKS: always break into small steps, one tool call at a time:
1. Read input files first
2. Write a Python script to a .py file using write_file + STARTCONTENT
3. Run it: {{"name": "bash", "arguments": {{"command": "python3 script.py"}}}}
4. Verify output by reading the result file
Never try to do everything in one command. Never hardcode data you already read from files.

WHEN TO USE A TOOL:
- Only when the task literally requires it: reading a file, running a command, searching the web, git operations.
- Do NOT use a tool for: greetings, math, general knowledge, definitions, coding questions, or anything you can answer directly.
- When the request is genuinely ambiguous and you cannot proceed safely: call the ask_user tool. Do NOT ask the question in plain text and do NOT guess.
  Example: user says "delete the file" without naming one → output {{"name": "ask_user", "arguments": {{"question": "Which file should I delete?"}}}}

WHEN NOT TO USE A TOOL: just reply in plain text:
- "hello" → reply normally
- "what is 2+2" → reply "4"
- "explain recursion" → reply with explanation
- Never output {{"name": "none"}}. If no tool is needed, just write plain text.
"""


def _build_tool_descriptions(tools: list[dict]) -> str:
    lines = []
    for t in tools:
        fn = t["function"]
        props = fn.get("parameters", {}).get("properties", {})
        args = ", ".join(
            f"{k}: {v.get('description', v.get('type', ''))}"
            for k, v in props.items()
        )
        lines.append(f"- {fn['name']}({args}): {fn['description']}")
    return "\n".join(lines)


def _uses_native_tools(model: str) -> bool:
    return any(model.startswith(p) for p in _NATIVE_TOOL_CALL_PREFIXES)


def _parse_heredoc_tool_call(text: str) -> list[dict] | None:
    if "STARTCONTENT" not in text:
        return None
    try:
        # Strip fenced code block wrappers
        clean = re.sub(r"```[a-z]*\n?(.*?)\n?```", r"\1", text, flags=re.DOTALL)
        text = clean if "STARTCONTENT" in clean else text

        calls = []
        parts = text.split("STARTCONTENT")
        for i, part in enumerate(parts[:-1]):
            # Find the JSON header before this STARTCONTENT
            brace = part.rfind("{")
            if brace == -1:
                continue
            header_json = part[brace:].strip().splitlines()[0]
            try:
                obj = json.loads(header_json)
            except json.JSONDecodeError:
                continue
            name = obj.get("name") or obj.get("function")
            if name not in ("write_file", "append_file"):
                continue
            path = obj.get("arguments", {}).get("path", "")
            if not path:
                continue
            after = parts[i + 1]
            if "ENDCONTENT" in after:
                content, _ = after.split("ENDCONTENT", 1)
            else:
                content = after
            content = content.strip("\n")
            if not content:
                continue
            calls.append({
                "id": f"heredoc_call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"path": path, "content": content})},
            })
        return calls[:1] if calls else None
    except Exception:
        return None


def _parse_all_text_tool_calls(text: str) -> list[dict] | None:
    calls = []
    # Try whole text first (handles multi-line JSON tool calls)
    result = _parse_text_tool_call(text)
    if result:
        result[0]["id"] = "text_call_0"
        return result
    # Fallback: scan line by line for single-line tool calls
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        result = _parse_text_tool_call(line)
        if result:
            result[0]["id"] = f"text_call_{len(calls)}"
            calls.extend(result)
    return calls if calls else None


def _parse_text_tool_call(text: str) -> list[dict] | None:
    text = text.strip()
    # Find the first '{'; JSON may be preceded by reasoning text
    brace = text.find("{")
    if brace == -1:
        return None
    text = text[brace:]
    # Extract just the first complete JSON object (depth-counting)
    depth, in_str, escape = 0, False, False
    end = 0
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
                    end = i + 1
                    break
    try:
        obj = json.loads(text[:end] if end else text)
        if not isinstance(obj, dict):
            return None
        name = (
            obj.get("name")
            or obj.get("function")
            or obj.get("tool")
            or obj.get("tool_name")
        )
        if not name or not isinstance(name, str) or name.lower() in ("none", "null", ""):
            return None
        raw_args = (
            obj.get("arguments")
            or obj.get("parameters")
            or obj.get("args")
            or obj.get("input")
            or {}
        )
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (json.JSONDecodeError, ValueError):
                # Wrap bare string as the primary argument for known tools
                _STR_ARG = {"bash": "command", "web_search": "query", "web_fetch": "url",
                            "read_file": "path", "write_file": "path", "list_dir": "path"}
                key = _STR_ARG.get(name, "input")
                raw_args = {key: raw_args}
        if not isinstance(raw_args, dict):
            return None
        return [{
            "id": "text_call_0",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(raw_args)},
        }]
    except (json.JSONDecodeError, ValueError):
        # Fallback: if it looks like a write_file/append_file call with broken JSON,
        # extract path and use raw content between first and last quote of content value
        return _salvage_write_call(text[:end] if end else text)


def _salvage_write_call(text: str) -> list[dict] | None:
    """Last-resort parser for write_file/append_file with unescaped quotes in content."""
    name_m = re.search(r'"name"\s*:\s*"(write_file|append_file)"', text)
    if not name_m:
        return None
    name = name_m.group(1)
    path_m = re.search(r'"path"\s*:\s*"([^"]+)"', text)
    if not path_m:
        return None
    path = path_m.group(1)
    # Extract content: everything between first "content": " and the last "
    content_m = re.search(r'"content"\s*:\s*"(.*)', text, re.DOTALL)
    if not content_m:
        return None
    raw = content_m.group(1)
    # Strip trailing JSON closing characters
    raw = re.sub(r'"\s*\}?\s*\}?\s*$', '', raw)
    # Unescape \n, \t
    content = raw.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    if not content.strip():
        return None
    return [{
        "id": "salvage_call_0",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"path": path, "content": content})},
    }]


def _unwrap_json_text(text: str) -> str:
    if not text.startswith("{"):
        return text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for key in ("content", "response", "message", "text", "answer", "output"):
                if isinstance(obj.get(key), str):
                    return obj[key]
    except (json.JSONDecodeError, ValueError):
        pass
    return text


class Agent:
    def __init__(self, config: dict):
        self.config = config
        self.model = config["model"]
        self._base_system_prompt = config.get("system_prompt", "You are a helpful assistant.")
        self.show_thinking = config.get("show_thinking", False)
        self.history: list[dict] = []
        self.max_turns = config.get("max_history_turns", 50)

        if config.get("ollama_base_url"):
            os.environ.setdefault("OLLAMA_API_BASE", config["ollama_base_url"])

        for key, env in [
            ("openai", "OPENAI_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("groq", "GROQ_API_KEY"),
        ]:
            val = config.get("api_keys", {}).get(key)
            if val:
                os.environ.setdefault(env, val)

    @property
    def system_prompt(self) -> str:
        return self._base_system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._base_system_prompt = value

    def switch_model(self, model: str) -> None:
        self.model = model
        # Apply per-model thinking default from config
        model_defaults = self.config.get("model_defaults", {})
        short = model.split("/")[-1]
        for pattern, defaults in model_defaults.items():
            if pattern in model or pattern in short:
                if "show_thinking" in defaults:
                    self.show_thinking = defaults["show_thinking"]
                break

    def clear_history(self) -> None:
        self.history = []

    def _effective_system_prompt(self, tools: list[dict]) -> str:
        """For models without native tool calling, append tool instructions."""
        system = self._base_system_prompt + _load_project_context() + _load_memories() + _env_context()
        if not tools or _uses_native_tools(self.model):
            return system
        tool_list = _build_tool_descriptions(tools)
        return system + _TOOL_PROMPT_TEMPLATE.format(tool_list=tool_list)

    def _estimate_tokens(self, messages: list[dict]) -> int:
        total = sum(len(str(m.get("content", ""))) for m in messages)
        return total // 4  # rough chars-to-tokens ratio

    def _build_messages(self, user_input: str, tools: list[dict], images: list[str] | None = None) -> list[dict]:
        history = self.history[-self.max_turns * 2:]
        messages = [{"role": "system", "content": self._effective_system_prompt(tools)}]
        messages += history
        if images:
            blocks: list[dict] = [{"type": "text", "text": user_input}]
            for path in images:
                block = _encode_image(path)
                if block:
                    blocks.append(block)
            messages.append({"role": "user", "content": blocks})
        else:
            messages.append({"role": "user", "content": user_input})
        return messages

    def context_usage(self) -> tuple[int, int]:
        ctx = self.config.get("num_ctx", self.config.get("max_tokens", 8192))
        tools = self._get_tools()
        messages = self._build_messages("", tools)
        est = self._estimate_tokens(messages)
        return est, ctx

    def _get_tools(self) -> list[dict]:
        return get_tool_schemas(self.config.get("tools_enabled"))

    def chat(
        self,
        user_input: str,
        on_token: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
        think: bool | None = None,
        complexity_hint: str | None = None,
        images: list[str] | None = None,
    ) -> tuple[str, int]:
        self._maybe_summarize_history(complexity_hint or user_input)
        tools = self._get_tools()

        if self.config.get("script_mode", True) and self._looks_complex(complexity_hint or user_input):
            result, last_error = self._try_script_approach(user_input, on_token, on_tool_call, on_tool_result)
            if result is not None:
                self.history.append({"role": "user", "content": user_input})
                self.history.append({"role": "assistant", "content": result})
                return result, 0, {}
            # Script approach failed; fall through to tool loop with error context
            if last_error:
                user_input = f"{user_input}\n\n[Previous script attempt failed: {last_error}]"

        messages = self._build_messages(user_input, tools, images=images)
        self.history.append({"role": "user", "content": user_input})
        final_response, thinking_tokens, usage = self._run_loop(messages, tools, on_token, on_thinking, on_tool_call, on_tool_result, think)
        final_response = final_response or ""
        self.history.append({"role": "assistant", "content": final_response})
        return final_response, thinking_tokens, usage

    _COMPLEX_PATTERNS = re.compile(
        r"\b(loop|repeat|iterate|for each|step\s*\d|multi.?step|compute|calculate|parse|process|count|total|sum|compare|report)\b",
        re.IGNORECASE,
    )
    # File edit tasks: "change X to Y in file", "replace X with Y", "update X in file", etc.
    _EDIT_PATTERNS = re.compile(
        r"\b(change|replace|update|rename|modify|correct|fix)\b.{0,120}\b(in|inside|within|to|from)\b",
        re.IGNORECASE,
    )

    _SIMPLE_EDIT_PATTERNS = re.compile(
        r"\b(change|replace|rename|update)\b.{0,60}\bword\b|\bchange\b.{0,30}\bto\b.{0,30}\b(word|string|text|name)\b",
        re.IGNORECASE,
    )

    def _looks_complex(self, text: str) -> bool:
        if self._SIMPLE_EDIT_PATTERNS.search(text):
            return False
        if self._EDIT_PATTERNS.search(text):
            return True
        return bool(self._COMPLEX_PATTERNS.search(text)) and len(text) > 100

    def _try_script_approach(self, user_input, on_token, on_tool_call, on_tool_result) -> tuple[str | None, str | None]:
        """Generate and run a Python script for the task. Retries up to 3 times on failure."""
        max_retries = 3
        last_error = None
        script = self._plan_script(user_input, on_token=on_token)
        if not script:
            return None, "script planning returned empty"

        def _file_snapshot():
            return {f: (os.path.getsize(f), os.path.getmtime(f)) for f in os.listdir(".") if os.path.isfile(f)}

        for attempt in range(max_retries):
            # Reject dummy/placeholder scripts
            real_lines = [l for l in script.splitlines() if l.strip() and not l.strip().startswith("#")]
            if len(real_lines) < 3:
                last_error = "script was empty or only comments"
                fix_prompt = (
                    f"Your previous script was empty or only had comments.\n"
                    f"Task: {user_input}\n\n"
                    "Write a complete Python script using only stdlib (csv, json, os, collections). "
                    "Output ONLY raw Python code, no markdown fences, no placeholder comments."
                )
                script = self._plan_script(fix_prompt, on_token=on_token)
                if not script:
                    break
                continue

            before = _file_snapshot()
            if on_tool_call:
                on_tool_call("bash", {"command": "python3 -"})
            try:
                proc = subprocess.run(
                    ["python3", "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                out = proc.stdout.rstrip()
                err = proc.stderr.rstrip()
                run_result = "\n".join(filter(None, [out, f"[stderr]\n{err}" if err else None]))
                if proc.returncode != 0 and not run_result:
                    run_result = f"(exit code {proc.returncode})"
                if not run_result:
                    run_result = "(exit 0, command succeeded)"
            except subprocess.TimeoutExpired:
                run_result = "Error: script timed out after 120s"
            except Exception as e:
                run_result = f"Error: {e}"
            if on_tool_result:
                on_tool_result("bash", run_result)
            after = _file_snapshot()

            has_error = "Traceback" in run_result or "Error" in run_result or "SyntaxError" in run_result
            # >10 bytes excludes empty stub files from being counted as output
            new_or_changed = [
                f for f, (sz, mt) in after.items()
                if (f not in before or before[f] != (sz, mt)) and sz > 10
            ]

            if not has_error and new_or_changed:
                output_note = f"Output files: {', '.join(new_or_changed)}" if new_or_changed else run_result.strip()
                summary = (
                    f"Executed script:\n"
                    f"```python\n{script}\n```\n\n"
                    f"{output_note}"
                ).strip()
                if on_token:
                    on_token(f"Done. {output_note}")
                return summary, None

            if has_error:
                last_error = run_result.strip()
                extra = ""
                if "FileNotFoundError" in run_result or "No such file" in run_result:
                    files = sorted(f for f in os.listdir(".") if os.path.isfile(f) and not f.startswith("_"))
                    extra = f"\n\nFiles available in the current directory: {files}\nUse the correct filename from this list."
                feedback = f"The script failed with this error:\n{run_result}{extra}\nFix the root cause. Do not catch or suppress exceptions."
            elif new_or_changed:
                last_error = "output files were empty or too small"
                feedback = "The script ran but output files are empty. Make sure the script writes meaningful content."
            else:
                last_error = "script produced no output files"
                feedback = "The script ran without errors but wrote no output files. Fix it to actually write the required output."

            fix_prompt = (
                f"This Python script has a problem:\n\n```python\n{script}\n```\n\n"
                f"{feedback}\n\n"
                "Output ONLY the corrected Python script using stdlib only. No markdown, no explanation."
            )
            script = self._plan_script(fix_prompt, on_token=on_token)
            if not script:
                break

        return None, last_error

    def _maybe_summarize_history(self, user_input: str = "") -> None:
        """Compact oldest turns into a summary when approaching the context limit."""
        ctx = self.config.get("num_ctx", self.config.get("max_tokens", 8192))
        tools = self._get_tools()

        # Compact in a loop; one pass may not be enough for very full histories
        for _ in range(5):
            if not self.history:
                break
            messages = self._build_messages(user_input, tools)
            est = self._estimate_tokens(messages)
            over_tokens = est >= ctx * 0.8
            over_turns = len(self.history) >= self.max_turns * 2 * 0.8
            if not over_tokens and not over_turns:
                break
            self._compact_history()

    def _compact_history(self) -> None:
        keep_from = len(self.history) // 2
        old_turns = self.history[:keep_from]
        self.history = self.history[keep_from:]

        old_text = "\n".join(
            f"User: {t['content']}" if t["role"] == "user" else f"Assistant: {t['content']}"
            for t in old_turns
        )
        summary_prompt = [
            {"role": "system", "content": "Summarize this conversation history concisely in 3-5 sentences."},
            {"role": "user", "content": old_text[:4000]},
        ]
        try:
            _, completion = _litellm()
            resp = completion(model=self.model, messages=summary_prompt, max_tokens=300, stream=False)
            summary = resp.choices[0].message.content or ""
            self.history.insert(0, {"role": "system", "content": f"[Earlier conversation summary: {summary}]"})
        except Exception:
            # If summarization fails, just drop the old turns
            pass

    def _plan_script(self, user_input: str, on_token=None) -> str | None:
        """Ask the model to write a runnable Python script for the task. Returns script text or None."""
        plan_system = (
            "You are a Python scripting assistant. "
            "When given a task, output ONLY a complete, runnable Python script that solves it. "
            "No explanation, no markdown fences, no tool calls. Just raw Python code. "
            "Rules:\n"
            "- Use ONLY Python stdlib: csv, json, os, math, collections, itertools, pathlib. "
            "Never import pandas, numpy, tqdm, or any third-party library.\n"
            "- Read input from files on disk. Write output to files on disk. Edit files in place, never create a copy with a new name unless explicitly asked.\n"
            "- Never write placeholder comments like '# cannot run'. Always write real code."
        )
        plan_messages = [
            {"role": "system", "content": plan_system},
            {"role": "user", "content": user_input},
        ]
        if self.model.startswith("ollama/"):
            text, _, _, _ = self._stream_ollama(plan_messages, False, on_token, None)
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            return text if text.strip() else None

        kwargs: dict = {
            "model": self.model,
            "messages": plan_messages,
            "stream": True,
            "max_tokens": 2048,
        }

        _, completion = _litellm()
        chunks = []
        try:
            for chunk in completion(**kwargs):
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    tok = delta.content
                    chunks.append(tok)
                    if on_token:
                        on_token(tok)
        except Exception:
            return None

        script = "".join(chunks).strip()
        if script.startswith("```"):
            script = re.sub(r"^```[a-z]*\n?", "", script)
            script = re.sub(r"\n?```$", "", script)
        return script if script else None

    def _run_loop(self, messages, tools, on_token, on_thinking, on_tool_call, on_tool_result, think=None) -> tuple[str, int, dict]:
        max_iterations = self.config.get("max_tool_iterations", 8)
        last_tool_error: dict[str, str] = {}
        last_tool_sig: str | None = None
        repeat_count = 0
        clarify_rounds = 0
        total_thinking_tokens = 0
        total_usage: dict = {"prompt": 0, "completion": 0, "total": 0}

        for iteration in range(max_iterations):
            response_text, thinking_text, tool_calls, usage = self._stream_response(
                messages, tools, on_token, on_thinking, think
            )
            if self.config.get("debug"):
                sys.stderr.write(f"\n[DEBUG iter={iteration}] text={repr(response_text[:200])} tools={[tc['function']['name'] for tc in tool_calls]}\n")
                sys.stderr.flush()
            total_thinking_tokens += len(thinking_text) // 4
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

            if not tool_calls:
                if _claims_file_change(response_text):
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": (
                        "[System] You claimed to modify a file but did not call edit_file or write_file. "
                        "The file has NOT been changed. You must call the appropriate tool now to make the change."
                    )})
                    continue
                # Small models ask for clarification in plain text instead of
                # calling ask_user. Catch that and route it through the same
                # clarification flow by synthesizing an ask_user call.
                if (clarify_rounds < _MAX_CLARIFY
                        and "ask_user" in get_all_tools()
                        and _looks_like_question(response_text)):
                    clarify_rounds += 1
                    tool_calls = [{
                        "id": "clarify",
                        "function": {
                            "name": "ask_user",
                            "arguments": json.dumps({"question": response_text.strip()}),
                        },
                    }]
                else:
                    return response_text, total_thinking_tokens, total_usage

            if _uses_native_tools(self.model):
                messages.append({
                    "role": "assistant",
                    "content": response_text or None,
                    "tool_calls": tool_calls,
                })
            else:
                messages.append({"role": "assistant", "content": response_text or ""})

            # Detect repeated identical tool calls (looping model)
            sig = str([(tc["function"]["name"], tc["function"].get("arguments","")) for tc in tool_calls])
            if sig == last_tool_sig:
                repeat_count += 1
                if repeat_count >= 1:
                    return response_text or "", total_thinking_tokens, total_usage
            else:
                repeat_count = 0
            last_tool_sig = sig

            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                if on_tool_call:
                    on_tool_call(name, args)

                result = dispatch_tool(name, args)

                # Post-write verification: confirm edit_file actually changed the file
                if name == "edit_file" and not result.startswith("Error") and "path" in args:
                    old_str = args.get("old_string", "")
                    new_str = args.get("new_string", "")
                    if old_str != new_str:  # skip verification for no-op edits
                        verify = dispatch_tool("read_file", {"path": args["path"]})
                        if old_str and old_str in verify:
                            result = f"VERIFICATION FAILED: old_string still present in file. Edit did not apply. Re-read the file and try again."
                        elif new_str and new_str not in verify:
                            result = f"VERIFICATION FAILED: new_string not found in file after edit. Re-read the file and try again."

                # If this tool already failed with the same error, tell the model to stop
                is_error = result.startswith("Error") or result.startswith("PERMANENT ERROR")
                if is_error and last_tool_error.get(name) == result:
                    result = f"PERMANENT ERROR, do not retry this tool: {result}"
                if is_error:
                    last_tool_error[name] = result
                else:
                    last_tool_error.pop(name, None)

                if on_tool_result:
                    on_tool_result(name, result)

                if _uses_native_tools(self.model):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", name),
                        "name": name,
                        "content": result,
                    })
                else:
                    messages.append({
                        "role": "user",
                        "content": f"[Tool result for {name}]\n{result}",
                    })
        return "[max tool iterations reached]", total_thinking_tokens, total_usage

    def _stream_ollama(self, messages, think, on_token, on_thinking) -> tuple[str, str, list[dict], dict]:
        """Direct streaming to Ollama /api/chat, no LiteLLM overhead."""
        base_url = self.config.get("ollama_base_url", "http://localhost:11434")
        model_name = self.model.split("/", 1)[-1]

        options: dict = {"num_ctx": self.config.get("num_ctx", 8192)}
        body: dict = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "options": options,
        }

        # think flag (Qwen3 / DeepSeek style)
        model_defaults = self.config.get("model_defaults", {})
        short = model_name
        think_override: bool | None = None
        for pattern, defaults in model_defaults.items():
            if pattern in self.model or pattern in short:
                if defaults.get("thinking") is False:
                    think_override = False
                elif defaults.get("thinking") is True:
                    think_override = True
                break
        if think is not None:
            think_override = think
        if think_override is False:
            body["think"] = False
        elif think_override is True:
            body["think"] = True

        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        response_chunks: list[str] = []
        thinking_chunks: list[str] = []
        in_thinking = False
        usage: dict = {}

        try:
            with urllib.request.urlopen(req) as resp:
                for raw_line in resp:
                    try:
                        obj = json.loads(raw_line.decode())
                    except (json.JSONDecodeError, ValueError):
                        continue

                    if obj.get("done"):
                        p = obj.get("prompt_eval_count", 0)
                        c = obj.get("eval_count", 0)
                        usage = {"prompt": p, "completion": c, "total": p + c}
                        break

                    msg = obj.get("message", {})
                    content: str = msg.get("content", "") or ""

                    # Structured thinking block (Qwen3 via Ollama returns thinking separately)
                    thinking: str = msg.get("thinking", "") or ""
                    if thinking:
                        thinking_chunks.append(thinking)
                        if on_thinking and self.show_thinking:
                            on_thinking(thinking)
                        continue

                    # Inline <think> tags
                    if "<think>" in content:
                        in_thinking = True
                        content = content.replace("<think>", "")
                    if "</think>" in content:
                        in_thinking = False
                        content = content.replace("</think>", "")
                    if in_thinking:
                        thinking_chunks.append(content)
                        if on_thinking and self.show_thinking:
                            on_thinking(content)
                        continue

                    if content:
                        response_chunks.append(content)
                        if on_token:
                            on_token(content)
        except KeyboardInterrupt:
            raise

        response_text = "".join(response_chunks).strip()
        thinking_text = "".join(thinking_chunks)

        heredoc_calls = _parse_heredoc_tool_call(response_text)
        if heredoc_calls:
            return "", thinking_text, heredoc_calls, usage

        text_tool_calls = _parse_all_text_tool_calls(response_text) or _parse_text_tool_call(response_text)
        if text_tool_calls:
            return "", thinking_text, text_tool_calls, usage

        return _unwrap_json_text(response_text), thinking_text, [], usage

    def _stream_response(
        self,
        messages: list[dict],
        tools: list[dict],
        on_token,
        on_thinking,
        think: bool | None = None,
    ) -> tuple[str, str, list[dict]]:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.config.get("max_tokens", 8192),
        }
        if tools and _uses_native_tools(self.model):
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"


        # Use direct Ollama path for local models, no LiteLLM overhead
        if self.model.startswith("ollama/") and not _uses_native_tools(self.model):
            return self._stream_ollama(messages, think, on_token, on_thinking)

        kwargs["stream_options"] = {"include_usage": True}

        response_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls_acc: dict[int, dict] = {}
        in_thinking = False
        usage: dict = {}

        _, completion = _litellm()
        last_err = None
        for attempt in range(3):
            try:
                stream = completion(**kwargs)
                break
            except Exception as e:
                last_err = e
                import time as _time
                _time.sleep(1.5 * (attempt + 1))
        else:
            error_msg = f"[LLM error after 3 attempts: {last_err}]"
            if on_token:
                on_token(error_msg)
            return error_msg, "", []

        try:
            for chunk in stream:
                if hasattr(chunk, "usage") and chunk.usage:
                    u = chunk.usage
                    usage["prompt"] = getattr(u, "prompt_tokens", 0)
                    usage["completion"] = getattr(u, "completion_tokens", 0)
                    usage["total"] = getattr(u, "total_tokens", 0)
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                _thinking_tok = (
                    (hasattr(delta, "thinking") and delta.thinking)
                    or (hasattr(delta, "reasoning_content") and delta.reasoning_content)
                )
                if _thinking_tok:
                    tok = delta.thinking if (hasattr(delta, "thinking") and delta.thinking) else delta.reasoning_content
                    thinking_chunks.append(tok)
                    if on_thinking and self.show_thinking:
                        on_thinking(tok)
                    continue

                content = delta.content or ""

                if "<think>" in content:
                    in_thinking = True
                    content = content.replace("<think>", "")
                if "</think>" in content:
                    in_thinking = False
                    content = content.replace("</think>", "")
                    if not content:
                        continue
                if in_thinking:
                    thinking_chunks.append(content)
                    if on_thinking and self.show_thinking:
                        on_thinking(content)
                    continue

                if content:
                    response_chunks.append(content)
                    if on_token:
                        on_token(content)

                try:
                    tc_list = delta.tool_calls
                except Exception:
                    tc_list = None
                if tc_list and _uses_native_tools(self.model):
                    for tc_delta in tc_list:
                        try:
                            idx = tc_delta.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc_delta.id or f"call_{idx}",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc = tool_calls_acc[idx]
                            if tc_delta.id:
                                tc["id"] = tc_delta.id
                            fn = tc_delta.function
                            if fn:
                                if fn.name:
                                    tc["function"]["name"] += fn.name
                                if fn.arguments:
                                    tc["function"]["arguments"] += fn.arguments
                        except Exception:
                            pass
        except KeyboardInterrupt:
            raise  # let _run_chat handle it and return to the prompt

        response_text = "".join(response_chunks).strip()
        thinking_text = "".join(thinking_chunks)

        if tool_calls_acc:
            return "", thinking_text, list(tool_calls_acc.values()), usage

        # For text-based models: parse tool call from response text
        heredoc_calls = _parse_heredoc_tool_call(response_text)
        if heredoc_calls:
            return "", thinking_text, heredoc_calls, usage

        text_tool_calls = _parse_all_text_tool_calls(response_text) or _parse_text_tool_call(response_text)
        if text_tool_calls:
            return "", thinking_text, text_tool_calls, usage

        return _unwrap_json_text(response_text), thinking_text, [], usage
