import subprocess
import sys
import tempfile
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, merge_completers
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.formatted_text import HTML

HISTORY_FILE = Path.home() / ".local" / "share" / "eros" / "input_history"

SLASH_COMMANDS = [
    ("/model",       "Switch model (e.g. ollama/llama3.2)"),
    ("/tools",       "List available tools"),
    ("/thinking",         "Toggle thinking output on/off"),
    ("/token-generated",  "Toggle live tok/s display"),
    ("/system",      "Override system prompt"),
    ("/remember",    "Save a fact to long-term memory (key: value)"),
    ("/forget",      "Remove memories matching a keyword"),
    ("/memories",    "List all stored memories"),
    ("/retry",       "Regenerate the last response"),
    ("/clear",       "Clear conversation history"),
    ("/history",     "Show turns in context"),
    ("/rooms",       "Browse and switch chat rooms (interactive)"),
    ("/room-new",    "Create a new room (named from first message)"),
    ("/room-delete", "Delete a room by name"),
    ("/search",      "Search across all rooms"),
    ("/help",        "Show all commands"),
    ("/exit",        "Quit"),
]


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        typed = text.lower()
        for cmd, desc in SLASH_COMMANDS:
            if cmd.startswith(typed):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )


_file_cache: tuple[str, float, list] = ("", 0.0, [])  # (cwd, mtime, entries)
_CACHE_TTL = 5.0  # seconds


def _get_file_entries(cwd: Path) -> list[tuple[str, str]]:
    """Return [(rel_path, size_label)] scanning max 3 levels deep, cached."""
    global _file_cache
    import time, os
    cwd_str = str(cwd)
    now = time.monotonic()
    if _file_cache[0] == cwd_str and now - _file_cache[1] < _CACHE_TTL:
        return _file_cache[2]

    entries = []
    skip = {"__pycache__", "node_modules", ".venv", ".git"}
    try:
        for root, dirs, files in os.walk(cwd):
            rel_root = Path(root).relative_to(cwd)
            depth = len(rel_root.parts)
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip]
            if depth >= 3:
                dirs.clear()
            for fname in files:
                if fname.startswith("."):
                    continue
                rel = str(rel_root / fname) if str(rel_root) != "." else fname
                fpath = Path(root) / fname
                try:
                    size = fpath.stat().st_size
                    meta = f"{size:,} B" if size < 1024 else f"{size // 1024} KB"
                except OSError:
                    meta = ""
                entries.append((rel, meta))
    except PermissionError:
        pass

    entries.sort(key=lambda x: x[0])
    _file_cache = (cwd_str, now, entries)
    return entries


class FileCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        at_pos = text.rfind("@")
        if at_pos == -1:
            return
        partial = text[at_pos + 1:]
        if not partial or len(partial) > 200 or " " in partial or "\n" in partial:
            return
        cwd = Path.cwd()
        # Stop suggesting once the partial already resolves to a real file
        try:
            if (cwd / partial).is_file():
                return
        except (OSError, ValueError):
            return
        low = partial.lower()
        count = 0
        for rel, meta in _get_file_entries(cwd):
            if low in rel.lower():
                yield Completion(rel, start_position=-len(partial),
                                 display=rel, display_meta=meta)
                count += 1
                if count >= 30:
                    break


_style = Style.from_dict({
    "bottom-toolbar":                           "bg:#1a1a2e fg:#6c7086",
    "bottom-toolbar.text":                      "bg:#1a1a2e fg:#6c7086",
    "prompt":                                   "bold cyan",
    "prompt-continuation":                      "fg:#555555",
    "frame.border":                             "fg:#313244",
    "completion-menu.completion":               "bg:#1e1e2e fg:#cdd6f4",
    "completion-menu.completion.current":       "bg:#89b4fa fg:#1e1e2e bold",
    "completion-menu.meta.completion":          "bg:#181825 fg:#6c7086",
    "completion-menu.meta.completion.current":  "bg:#74c7ec fg:#1e1e2e",
    "scrollbar.background":                     "bg:#313244",
    "scrollbar.button":                         "bg:#89b4fa",
})


_clip_images: list[str] = []  # actual paths, indexed by [image #N]


def _clipboard_image_path() -> tuple[str, str] | None:
    """Save macOS clipboard image to a temp file. Returns (path, placeholder) or None."""
    if sys.platform != "darwin":
        return None
    try:
        tmp = Path(tempfile.gettempdir()) / f"eros_clip_{int(time.time())}.png"
        result = subprocess.run(
            ["osascript", "-e", f"""
                try
                    set img to (the clipboard as «class PNGf»)
                    set fp to open for access POSIX file "{tmp}" with write permission
                    set eof of fp to 0
                    write img to fp
                    close access fp
                    return "ok"
                on error
                    return ""
                end try
            """],
            capture_output=True, text=True, timeout=3,
        )
        if result.stdout.strip() == "ok" and tmp.exists() and tmp.stat().st_size > 0:
            _clip_images.append(str(tmp))
            return str(tmp), f"[image #{len(_clip_images)}]"
    except Exception:
        pass
    return None


def _clipboard_text() -> str:
    """Return macOS clipboard text content."""
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return ""


def _make_bindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter", filter=completion_is_selected)
    def _enter_apply_and_submit(event):
        buf = event.current_buffer
        cs = buf.complete_state
        if cs and cs.current_completion:
            buf.apply_completion(cs.current_completion)
        buf.validate_and_handle()

    @kb.add("enter", filter=has_completions & ~completion_is_selected)
    def _enter_select_first(event):
        event.current_buffer.complete_next()

    @kb.add("enter", filter=~has_completions & ~completion_is_selected)
    def _enter_submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("backspace")
    def _backspace(event):
        import re
        buf = event.current_buffer
        before = buf.document.text_before_cursor
        m = re.search(r'\[image #\d+\]$', before)
        if m:
            buf.delete_before_cursor(len(m.group(0)))
        else:
            buf.delete_before_cursor(1)

    @kb.add("c-v")
    def _paste(event):
        result = _clipboard_image_path()
        if result:
            _, placeholder = result
            event.current_buffer.insert_text(placeholder)
        else:
            text = _clipboard_text()
            if text:
                event.current_buffer.insert_text(text)

    return kb


def make_session() -> PromptSession:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=None,
        completer=merge_completers([SlashCompleter(), FileCompleter()]),
        complete_while_typing=True,
        key_bindings=_make_bindings(),
        style=_style,
        multiline=True,
        bottom_toolbar=" ",
    )


def get_input(session: PromptSession, prompt: str = "❯ ") -> str:
    return session.prompt(
        prompt,
        prompt_continuation=lambda width, line_number, wrap_count: "  ",
    )


def make_prompt() -> str:
    return HTML("<ansicyan><b>❯</b></ansicyan> ")
