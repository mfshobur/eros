#!/usr/bin/env python3
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from prompt_toolkit import PromptSession
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from config import load_config, save_default_model
from agent import Agent, _IMAGE_EXTS
from ui.input import _clip_images
from tools.base import load_tools, get_all_tools, set_permission_callback, set_permission_mode, get_permission_mode
from tools.bash import set_confirm_callback
from tools.interaction import set_ask_callback
from rich import box as _box
from rich.panel import Panel
from rich.rule import Rule

from ui.console import (
    console,
    print_welcome,
    print_help,
    print_tool_call,
    print_tool_result,
    print_error,
    print_info,
    confirm,
)
from ui.input import make_session, get_input, make_prompt
from ui.picker import pick_room, pick_model, PickResult
import memory.rooms as rooms

import json
import urllib.request

app = typer.Typer(add_completion=False)


def _start_telegram(config: dict) -> None:
    try:
        import telegram_bot
    except ImportError:
        return  # python-telegram-bot not installed
    token = config.get("telegram", {}).get("token", "")
    if token:
        telegram_bot.run_in_thread(config)
        print("Telegram bot running in background.")
    else:
        telegram_bot.setup_interactive(config)


def _ollama_model_ids(base_url: str) -> set[str]:
    with urllib.request.urlopen(base_url + "/api/tags", timeout=2) as r:
        data = json.loads(r.read())
    return {f"ollama/{m['name']}" for m in data.get("models", [])}

_NEW_ROOM_PREFIX = "new-"

_TOOL_LABELS = {
    "bash": ("Run command", "cyan"),
    "write_file": ("Write file", "yellow"),
    "edit_file": ("Edit file", "yellow"),
    "append_file": ("Append file", "yellow"),
}


def _permission_ui(tool_name: str, args: dict, preview: str) -> bool:
    """Show a confirmation panel and return True if the user approves."""
    from rich.syntax import Syntax
    label, color = _TOOL_LABELS.get(tool_name, (tool_name, "white"))
    path = args.get("path", "")
    header = f"[bold {color}]{label}[/bold {color}]"
    if path:
        header += f"  [dim]{path}[/dim]"
    console.print()
    if tool_name == "bash":
        console.print(Panel(
            Syntax(preview, "bash", theme="monokai", word_wrap=True),
            title=header,
            border_style=color,
            padding=(0, 1),
        ))
    elif tool_name == "edit_file":
        console.print(Panel(
            Syntax(preview, "diff", theme="monokai", word_wrap=True),
            title=header,
            border_style=color,
            padding=(0, 1),
        ))
    else:
        console.print(Panel(
            preview,
            title=header,
            border_style=color,
            padding=(0, 1),
        ))
    return confirm("Allow?", default=True)


def _fmt_elapsed(seconds: float) -> str:
    if seconds >= 60:
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m}m {s:.0f}s"
    return f"{seconds:.1f}s"


def _slugify(text: str, max_len: int = 32) -> str:
    """Turn a user message into a short kebab-case room name."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "room"


_INLINE_SIZE_LIMIT = 20000  # chars; inline small files, hint-only for large ones

_EDIT_INTENT = re.compile(
    r"\b(change|update|fix|replace|rename|edit|modify|correct|rewrite|remove|delete|add|insert|append)\b",
    re.IGNORECASE,
)


def _file_inline(path: Path) -> str:
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return ""
    if len(content) <= _INLINE_SIZE_LIMIT:
        return f'### {path}\n```\n{content.rstrip()}\n```'
    return f'### {path}\n(file too large to inline; use read_file with start_line/end_line to read in chunks)'


def _resolve_clip_refs(text: str) -> tuple[str, list[str]]:
    """Replace [image #N] placeholders with actual paths from clipboard history."""
    images: list[str] = []
    def replacer(m):
        n = int(m.group(1)) - 1
        if 0 <= n < len(_clip_images):
            images.append(_clip_images[n])
            return ""
        return m.group(0)
    cleaned = re.sub(r'\[image #(\d+)\]', replacer, text).strip()
    return cleaned, images


def _extract_images(text: str) -> tuple[str, list[str]]:
    """Extract @image.ext refs from text. Returns (text_without_image_refs, [paths])."""
    images: list[str] = []
    def replacer(m):
        if m.group(0).startswith("\\@"):
            return m.group(0)
        path = Path(m.group(1))
        if path.suffix.lower() in _IMAGE_EXTS and path.exists():
            images.append(str(path))
            return ""
        return m.group(0)
    cleaned = re.sub(r"\\?@([^\s,;:!?\"']+)", replacer, text).strip()
    return cleaned, images


def _expand_file_refs(text: str) -> str:
    def at_replacer(m):
        if m.group(0).startswith("\\@"):
            return m.group(0)[1:]
        path = Path(m.group(1))
        if not path.exists():
            return m.group(0)
        result = _file_inline(path)
        return result if result else m.group(0)
    def at_folder_replacer(m):
        if m.group(0).startswith("\\@"):
            return m.group(0)[1:]
        path = Path(m.group(1))
        if not path.is_dir():
            return m.group(0)
        parts = []
        for f in sorted(path.rglob("*"))[:20]:
            if f.is_file() and not f.name.startswith("."):
                r = _file_inline(f)
                if r:
                    parts.append(r)
        return "\n\n".join(parts) if parts else m.group(0)
    text = re.sub(r"\\?@([^\s,;:!?\"']+)", lambda m: at_folder_replacer(m) if Path(m.group(1)).is_dir() else at_replacer(m), text)

    def bare_replacer(m):
        path = Path(m.group(0))
        if not path.exists() or not path.is_file():
            return m.group(0)
        result = _file_inline(path)
        return result if result else m.group(0)
    text = re.sub(r"\b[\w\-]+\.\w{1,10}\b", bare_replacer, text)

    return text


def handle_slash_command(cmd: str, agent: Agent, state: dict) -> None:
    parts = cmd.strip().split(None, 1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command == "/exit":
        print_info("Goodbye.")
        raise SystemExit(0)

    elif command == "/help":
        print_help()

    elif command == "/clear":
        agent.clear_history()
        rooms.clear_room(state["room"])
        print_info(f"History cleared for room [cyan]{state['room']}[/cyan].")

    elif command == "/model":
        if not arg:
            ollama_url = agent.config.get("ollama_base_url", "http://localhost:11434")
            chosen = pick_model(agent.model, ollama_url)
            if chosen and chosen != agent.model:
                agent.switch_model(chosen)
                rooms.save_meta(state["room"], {"model": chosen})
                save_default_model(chosen)
                print_info(f"Switched to: [cyan]{chosen}[/cyan]")
            elif chosen == agent.model:
                print_info(f"Kept model: [cyan]{agent.model}[/cyan]")
        else:
            agent.switch_model(arg)
            rooms.save_meta(state["room"], {"model": arg})
            save_default_model(arg)
            print_info(f"Switched to: [cyan]{arg}[/cyan]")

    elif command == "/system":
        if not arg:
            print_info(f"System prompt:\n{agent.system_prompt}")
        else:
            agent.system_prompt = arg
            print_info("System prompt updated.")

    elif command == "/thinking":
        agent.show_thinking = not agent.show_thinking
        print_info(f"Thinking: [bold]{'ON' if agent.show_thinking else 'OFF'}[/bold]  (takes effect on next message)")

    elif command == "/token-generated":
        state["show_tps"] = not state.get("show_tps", False)
        if state["show_tps"]:
            print_info("Token speed: [bold green]ON[/bold green]")
        else:
            print_info("Token speed: [bold]OFF[/bold]")

    elif command == "/permissions":
        current = get_permission_mode()
        new_mode = "auto" if current == "manual" else "manual"
        set_permission_mode(new_mode)
        if new_mode == "manual":
            print_info("Permissions: [bold yellow]manual[/bold yellow]  you will approve every bash command and file write.")
        else:
            print_info("Permissions: [bold green]auto[/bold green]  only dangerous commands require approval.")

    elif command == "/tools":
        tool_map = get_all_tools()
        if not tool_map:
            print_info("No tools loaded.")
        else:
            console.print("\n[bold]Available tools:[/bold]")
            for name, tool in tool_map.items():
                console.print(f"  [cyan]{name}[/cyan]  [dim]{tool.description[:70]}[/dim]")
            console.print()

    elif command == "/history":
        n = len(agent.history)
        print_info(f"{n} messages in history ({n // 2} turns), room: [cyan]{state['room']}[/cyan]")

    # ── Room management ──────────────────────────────────────────────────────
    elif command == "/rooms":
        room_list = rooms.list_rooms()
        if not room_list:
            print_info("No rooms yet. Start chatting or use /room-new.")
            return
        result: PickResult = pick_room(room_list, state["room"], console)
        if result.action == "select":
            _switch_room(agent, state, result.name)
            console.clear()
            print_welcome(agent.model, list(get_all_tools().keys()))
            print_info(f"  Switched to room [cyan]{result.name}[/cyan]  ({len(agent.history) // 2} turns)")
            _print_room_history(result.name)
        elif result.action == "rename":
            # Rename already applied inside picker; just update state if current room was renamed
            if state["room"] == result.name:
                state["room"] = result.new_name
            print_info(f"Renamed [cyan]{result.name}[/cyan] → [cyan]{result.new_name}[/cyan]")

    elif command == "/room-new":
        temp = _new_temp_name()
        _switch_room(agent, state, temp)
        state["auto_rename"] = True
        console.clear()
        print_welcome(agent.model, list(get_all_tools().keys()))

    elif command == "/search":
        if not arg:
            print_error("Usage: /search <query>")
        else:
            matches = rooms.search_rooms(arg)
            if not matches:
                print_info(f"No results for [cyan]{arg}[/cyan]")
            else:
                console.print(f"\n[bold]Search results for '[cyan]{arg}[/cyan]':[/bold]  {len(matches)} match(es)\n")
                for m in matches[:20]:
                    console.print(f"  [dim]{m['ts']}[/dim]  [cyan]{m['room']}[/cyan]")
                    console.print(f"  [dim]You:[/dim] {m['user']}")
                    console.print(f"  [dim]AI:[/dim]  {m['assistant']}")
                    console.print()

    elif command == "/room-delete":
        if not arg:
            print_error("Usage: /room-delete <name>")
        elif arg == state["room"]:
            print_error("Cannot delete the active room. Switch first.")
        elif rooms.delete_room(arg):
            print_info(f"Deleted room: [cyan]{arg}[/cyan]")
        else:
            print_error(f"Room not found: {arg}")

    elif command == "/remember":
        if not arg or ": " not in arg:
            print_error("Usage: /remember key: value  (e.g. /remember name: Shobur)")
        else:
            cap = agent.config.get("max_memories", 20)
            if rooms.save_memory(arg, max_memories=cap):
                print_info(f"Remembered: [cyan]{arg}[/cyan]")
            else:
                print_error(f"Memory full ({cap} entries). Use /forget <keyword> to free space.")

    elif command == "/forget":
        if not arg:
            print_error("Usage: /forget <keyword>")
        else:
            n = rooms.delete_memory(arg)
            print_info(f"Removed {n} memory entry(s) matching '[cyan]{arg}[/cyan]'.")

    elif command == "/memories":
        facts = rooms.load_memories()
        if not facts:
            print_info("No memories stored. Use /remember key: value to add one.")
        else:
            console.print("\n[bold]Memories:[/bold]")
            for f in facts:
                console.print(f"  [dim]·[/dim] {f}")
            console.print()

    elif command == "/tsave":
        parts = arg.split(None, 1)
        if len(parts) < 2:
            print_error("Usage: /tsave <name> <prompt text>")
        else:
            rooms.save_template(parts[0], parts[1])
            print_info(f"Template saved: [cyan]{parts[0]}[/cyan]")

    elif command == "/t":
        if not arg:
            print_error("Usage: /t <name>")
        else:
            prompt = rooms.get_template(arg)
            if prompt is None:
                print_error(f"Template not found: {arg}")
            else:
                _run_chat(agent, state, prompt, original_input=prompt, raw_input=prompt)
                state["last_turn"] = {"expanded": prompt, "original_input": prompt, "raw_input": prompt, "images": []}

    elif command == "/templates":
        templates = rooms.load_templates()
        if not templates:
            print_info("No templates. Use /tsave <name> <prompt> to create one.")
        else:
            console.print("\n[bold]Templates:[/bold]")
            for t in templates:
                preview = t["prompt"][:60] + ("…" if len(t["prompt"]) > 60 else "")
                console.print(f"  [cyan]{t['name']}[/cyan]  [dim]{preview}[/dim]")
            console.print()

    elif command == "/tdelete":
        if not arg:
            print_error("Usage: /tdelete <name>")
        else:
            if rooms.delete_template(arg):
                print_info(f"Deleted template: [cyan]{arg}[/cyan]")
            else:
                print_error(f"Template not found: {arg}")

    elif command == "/retry":
        last = state.get("last_turn")
        if not last:
            print_error("Nothing to retry.")
            return
        if len(agent.history) >= 2:
            agent.history = agent.history[:-2]
        print_info("Retrying last message...")
        _run_chat(
            agent, state,
            last["expanded"],
            original_input=last["original_input"],
            raw_input=last["raw_input"],
            images=last["images"],
        )

    elif command == "/export":
        from datetime import datetime
        turns = rooms.load_turns(state["room"], max_turns=9999)
        if not turns:
            print_error("No conversation to export.")
            return
        filename = arg.strip() if arg else f"{state['room']}-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
        if not filename.endswith(".md"):
            filename += ".md"
        lines = [f"# {state['room']}\n"]
        for t in turns:
            ts = t.get("ts", "")[:16].replace("T", " ")
            model = t.get("model", "")
            lines.append(f"## [{ts}] ({model})\n")
            lines.append(f"**You:** {t.get('user', '')}\n")
            if t.get("tools"):
                for tool in t["tools"]:
                    lines.append(f"> tool: `{tool['name']}`\n")
            lines.append(f"**AI:** {t.get('assistant', '')}\n")
        Path(filename).write_text("\n".join(lines))
        print_info(f"Exported {len(turns)} turns to [cyan]{filename}[/cyan]")

    else:
        print_error(f"Unknown command: {command}. Type /help for a list.")


def _switch_room(agent: Agent, state: dict, name: str) -> None:
    state["room"] = name
    state.pop("auto_rename", None)
    agent.history = rooms.load_messages(name, agent.max_turns)
    meta = rooms.load_meta(name)
    if "model" in meta:
        agent.switch_model(meta["model"])
    rooms.save_last_room(name)


def _print_room_history(room: str, max_turns: int = 5) -> None:
    turns = rooms.load_turns(room, max_turns)
    if not turns:
        return
    console.print(Rule(f"[dim]last {len(turns)} turns[/dim]", style="dim"))
    for t in turns:
        ts = t.get("ts", "")[:16].replace("T", " ")
        user_msg = t.get("user", "")
        asst_msg = t.get("assistant", "")
        console.print(f"[dim]{ts}[/dim]")
        console.print(
            Panel(user_msg, box=_box.SIMPLE, style="on grey19", border_style="grey30", padding=(0, 1))
        )
        if asst_msg:
            console.print(Markdown(asst_msg))
        console.print()
    console.print(Rule(style="dim"))
    console.print()


def _new_temp_name() -> str:
    return f"{_NEW_ROOM_PREFIX}{datetime.now().strftime('%m%d-%H%M%S')}"


@app.command()
def main(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model from config"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    system: Optional[str] = typer.Option(None, "--system", "-s", help="Override system prompt"),
    no_tools: bool = typer.Option(False, "--no-tools", help="Disable all tools"),
    room: Optional[str] = typer.Option(None, "--room", "-r", help="Start in this room"),
):
    config = load_config(config_path)
    if model:
        config["model"] = model
    if system:
        config["system_prompt"] = system
    if no_tools:
        config["tools_enabled"] = []

    load_tools(config.get("tools_enabled", []), config=config)
    set_confirm_callback(confirm)
    set_permission_callback(_permission_ui)
    initial_mode = config.get("permission_mode", "auto")
    set_permission_mode(initial_mode)
    rooms.init()

    _start_telegram(config)

    agent = Agent(config)
    for _ctx in ("EROS.md", "CLAUDE.md"):
        if Path(_ctx).exists():
            print_info(f"  📎 Project context: [cyan]{_ctx}[/cyan]")
            break
    _mem_count = len(rooms.load_memories())
    if _mem_count:
        print_info(f"  🧠 {_mem_count} memory(s) loaded")
    last_room = rooms.load_last_room()
    if room:
        current_room = room
        auto_rename = False
    elif last_room:
        current_room = last_room
        auto_rename = False
    else:
        current_room = _new_temp_name()
        auto_rename = True
    state: dict = {"room": current_room, "auto_rename": auto_rename}

    agent.history = rooms.load_messages(current_room, agent.max_turns)
    ollama_url = config.get("ollama_base_url", "http://localhost:11434")
    try:
        available_models = _ollama_model_ids(ollama_url)
    except Exception:
        available_models = set()

    if not model:  # don't override an explicit --model flag
        meta = rooms.load_meta(current_room)
        if "model" in meta:
            room_model = meta["model"]
            if room_model.startswith("ollama/"):
                if room_model in available_models:
                    agent.switch_model(room_model)
            else:
                agent.switch_model(room_model)

    if agent.model.startswith("ollama/"):
        if not available_models:
            print_error(f"Ollama not reachable at {ollama_url}. Start it with: ollama serve")
        elif agent.model not in available_models:
            print_error(f"Model [cyan]{agent.model}[/cyan] not found in Ollama.")
            chosen = pick_model(agent.model, ollama_url)
            if chosen:
                agent.switch_model(chosen)
                config["model"] = chosen
                save_default_model(chosen)
            else:
                print_error("No model selected, exiting.")
                raise SystemExit(1)

    print_welcome(agent.model, list(get_all_tools().keys()))
    if rooms.room_exists(current_room):
        print_info(f"  Resumed room [cyan]{current_room}[/cyan]  ({len(agent.history) // 2} turns)")
        _print_room_history(current_room)
    elif not auto_rename:
        print_info(f"  New room [cyan]{current_room}[/cyan]")

    session: PromptSession = make_session()

    while True:
        try:
            if sys.stdout.isatty():
                short_model = agent.model.split("/")[-1]
                room = state['room']
                w = console.width
                full_label = f" {room}  {short_model} "
                if len(full_label) > w - 4:
                    full_label = f" {short_model} "
                if len(full_label) > w - 4:
                    full_label = f" {short_model[:max(4, w - 6)]}… "
                line_len = w - len(full_label)
                line = "─" * line_len
                console.print(f"[cyan]{line}[/cyan][bold cyan]{full_label}[/bold cyan]")
            user_input = get_input(session, make_prompt()).strip()
        except (KeyboardInterrupt, EOFError):
            print_info("\nGoodbye.")
            raise SystemExit(0)

        if not user_input:
            continue

        if user_input.startswith("/"):
            handle_slash_command(user_input, agent, state)
            continue

        erase_lines = user_input.count("\n") + 1
        sys.stdout.write(f"\033[{erase_lines}A\033[J")
        sys.stdout.flush()
        console.print(
            Panel(user_input, box=_box.SIMPLE, style="on grey19", border_style="grey30", padding=(0, 1))
        )

        # /no_think and /think suffixes: pass through to model (Qwen3 chat template
        # recognises these tokens), but strip from display/history via original_input
        display_input = user_input
        if user_input.lower().endswith("/no_think"):
            display_input = user_input[:-len("/no_think")].rstrip()
        elif user_input.lower().endswith("/think"):
            display_input = user_input[:-len("/think")].rstrip()

        text_input, clip_images = _resolve_clip_refs(user_input)
        text_input, file_images = _extract_images(text_input)
        images = clip_images + file_images
        expanded = _expand_file_refs(text_input)
        if images:
            print_info(f"  image: {', '.join(Path(p).name for p in images)}")
        elif expanded != text_input:
            found = [m for m in re.findall(r"@(\S+)", text_input) if Path(m).exists()]
            if found:
                print_info(f"  attached: {', '.join(found)}")

        _run_chat(agent, state, expanded, original_input=display_input, raw_input=user_input, images=images)
        state["last_turn"] = {"expanded": expanded, "original_input": display_input, "raw_input": user_input, "images": images}
        for p in clip_images:
            Path(p).unlink(missing_ok=True)
        console.print()


_EXT_MAP = {
    "html": "html", "css": "css", "javascript": "js", "js": "js",
    "typescript": "ts", "ts": "ts", "python": "py", "py": "py",
    "bash": "sh", "sh": "sh", "json": "json", "yaml": "yaml",
    "yml": "yml", "sql": "sql", "markdown": "md", "md": "md",
}

_CREATE_KEYWORDS = re.compile(
    r"\b(create|make|write|build|generate|design)\b.{0,60}"
    r"\b(page|file|script|component|template|site|app)\b",
    re.IGNORECASE,
)


def _auto_save_code_blocks(response: str, user_input: str) -> None:
    """If model pasted a code block instead of using write_file, auto-save it."""
    if not _CREATE_KEYWORDS.search(user_input):
        return
    blocks = re.findall(r"```(\w+)?\n(.*?)```", response, re.DOTALL)
    if not blocks:
        return
    # Only auto-save if there's exactly one substantial block
    substantial = [(lang, code) for lang, code in blocks if len(code.strip()) > 100]
    if len(substantial) != 1:
        return
    lang, code = substantial[0]
    ext = _EXT_MAP.get(lang.lower(), "txt") if lang else "txt"
    # Try to infer filename from user input (e.g. "company-profile.html")
    fname_match = re.search(r"[\w\-]+\." + ext, user_input)
    filename = fname_match.group(0) if fname_match else f"output.{ext}"
    Path(filename).write_text(code.strip())
    print_info(f"  💾 auto-saved to [cyan]{filename}[/cyan]")


def _run_chat(agent: Agent, state: dict, user_input: str, original_input: str = "", raw_input: str = "", images: list[str] | None = None) -> None:
    started_at = time.monotonic()
    token_buffer: list[str] = []
    tool_log: list[dict] = []
    thinking_buffer: list[str] = []
    thinking_visible: list[bool] = [agent.show_thinking]
    first_token: list[bool] = [True]
    spinner_live: list[Live] = []
    md_live: list[Live] = []
    stream_start: list[float] = []
    token_count: list[int] = [0]

    spinner = Spinner("dots", style="cyan")

    def _stop_spinner() -> None:
        if spinner_live:
            spinner_live[0].update(Text(""))
            spinner_live[0].stop()
            spinner_live.clear()

    def _start_spinner() -> None:
        live = Live(spinner, console=console, refresh_per_second=15, transient=True)
        live.start()
        spinner_live.append(live)

    def _stop_md_live() -> None:
        if md_live:
            md_live[0].stop()
            md_live.clear()

    def on_token(text: str) -> None:
        token_buffer.append(text)
        token_count[0] += 1
        if first_token[0]:
            first_token[0] = False
            stream_start.append(time.monotonic())
            _stop_spinner()
            live = Live(
                Text(""),
                console=console,
                refresh_per_second=15,
                vertical_overflow="visible",
                transient=True,
            )
            live.start()
            md_live.append(live)
        if md_live:
            md_live[0].update(Text("".join(token_buffer) + " ▌"))

    def on_thinking(text: str) -> None:
        thinking_buffer.append(text)
        if agent.show_thinking:
            if first_token[0]:
                first_token[0] = False
                _stop_spinner()
            console.print(text, end="", markup=False, highlight=False, style="dim italic")

    def on_tool_call(name: str, args: dict) -> None:
        tool_log.append({"name": name, "args": args})
        _stop_md_live()
        _stop_spinner()
        token_buffer.clear()
        token_count[0] = 0
        stream_start.clear()
        first_token[0] = True
        print_tool_call(name, args)
        _start_spinner()

    def on_tool_result(name: str, result: str) -> None:
        for entry in reversed(tool_log):
            if entry["name"] == name and "result" not in entry:
                entry["result"] = result
                break
        _stop_spinner()
        print_tool_result(name, result)
        first_token[0] = True
        _start_spinner()

    def _ask(question: str, options: list | None = None) -> str:
        _stop_md_live()
        _stop_spinner()
        console.print()
        console.print(Panel(
            question,
            title="[bold magenta]Agent needs clarification[/bold magenta]",
            border_style="magenta",
            padding=(0, 1),
        ))
        if options:
            for i, opt in enumerate(options, 1):
                console.print(f"  [cyan]{i}[/cyan]. {opt}")
        answer = console.input("[bold]Your answer: [/bold]").strip()
        if options and answer.isdigit():
            idx = int(answer) - 1
            if 0 <= idx < len(options):
                answer = options[idx]
        _start_spinner()
        return answer or "(no answer given)"

    set_ask_callback(_ask)
    _start_spinner()

    result: dict = {}

    try:
        response, thinking_tokens, usage = agent.chat(
            user_input,
            on_token=on_token,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            complexity_hint=raw_input or user_input,
            images=images,
        )
        result = {"response": response, "thinking_tokens": thinking_tokens, "usage": usage}

        if state.get("auto_rename"):
            source = original_input or user_input
            new_name = _slugify(source)
            old_name = state["room"]
            if rooms.rename_room(old_name, new_name):
                state["room"] = new_name
            state.pop("auto_rename", None)

        rooms.save_turn(state["room"], agent.model, user_input, response, tools=tool_log if tool_log else None)
        rooms.save_meta(state["room"], {"model": agent.model})
        rooms.save_last_room(state["room"])

    except KeyboardInterrupt:
        _stop_md_live()
        _stop_spinner()
        partial = "".join(token_buffer).strip()
        if partial:
            result = {"response": partial, "thinking_tokens": 0, "usage": {}}
            print_info("  [yellow]⚠ Interrupted, partial response saved[/yellow]")
        else:
            print_info("\n  Interrupted.")
        return
    except Exception as e:
        print_error(str(e))
    finally:
        _stop_md_live()
        _stop_spinner()
        if token_buffer:
            console.print(Markdown("".join(token_buffer)))

    if result.get("response", "").strip():
        _auto_save_code_blocks(result["response"], user_input)

    elapsed = _fmt_elapsed(time.monotonic() - started_at)
    thinking_tokens = result.get("thinking_tokens", 0)
    usage = result.get("usage") or {}
    ctx_used, ctx_max = agent.context_usage()
    ctx_pct = int(ctx_used / ctx_max * 100) if ctx_max else 0
    ctx_warn = f"  [yellow]⚠ context {ctx_pct}%[/yellow]" if ctx_pct >= 70 else ""

    usage_str = ""
    if usage.get("prompt") or usage.get("completion"):
        usage_str = f"  ·  in:{usage.get('prompt',0)} out:{usage.get('completion',0)} tok"

    tps_str = ""
    if state.get("show_tps") and stream_start and token_count[0] > 0:
        stream_elapsed = time.monotonic() - stream_start[0]
        tps_str = f"  ·  {token_count[0] / max(stream_elapsed, 0.01):.1f} tok/s"

    if thinking_buffer:
        think_label = "on" if thinking_visible[0] else "off"
        think_info = f"  ·  thinking: {think_label}"
        if thinking_tokens:
            think_info += f" (~{thinking_tokens:,} tok)"
        console.print(f"[dim]  ⏱ {elapsed}{think_info}{usage_str}{tps_str}  ([bold]/thinking[/bold] to toggle)[/dim]{ctx_warn}")
    else:
        console.print(f"[dim]  ⏱ {elapsed}{usage_str}{tps_str}[/dim]{ctx_warn}")
    console.print()


if __name__ == "__main__":
    app()
