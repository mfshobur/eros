import io
import os
import select
import sys
import tty
import termios
import urllib.request
import json
from dataclasses import dataclass
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)  # bypass Python buffer so select works correctly
        if ch == b"\x1b":
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                return "esc"
            nxt = os.read(fd, 2)
            if nxt == b"[A": return "up"
            if nxt == b"[B": return "down"
            if nxt in (b"[C", b"[D"): return "ignore"
            return "esc"
        if ch == b"\x7f": return "backspace"
        if ch == b"\t":   return "tab"
        if ch == b"\x03": return "esc"
        return ch.decode("utf-8", errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _terminal_height() -> int:
    try:
        return os.get_terminal_size().lines
    except Exception:
        return 24


def _render_rooms(rooms: list[dict], idx: int, mode: str, rename_buf: str,
                  scroll: int, visible: int) -> tuple[str, int]:
    buf = io.StringIO()
    w = Console().width or 100
    tmp = Console(file=buf, highlight=False, markup=True, width=w)

    sel_sty = "bold cyan reverse" if mode == "select" else "dim"
    ren_sty = "bold cyan reverse" if mode == "rename" else "dim"
    tab_bar = Text()
    tab_bar.append("  Select  ", style=sel_sty)
    tab_bar.append("   ")
    tab_bar.append("  Rename  ", style=ren_sty)

    t = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    t.add_column("", width=2, no_wrap=True)
    t.add_column("Room", min_width=16, no_wrap=True)
    t.add_column("Last message", ratio=1)
    t.add_column("Time", style="dim", min_width=16, no_wrap=True)

    window = rooms[scroll:scroll + visible]
    for i, r in enumerate(window):
        abs_i = scroll + i
        cursor = "▶" if abs_i == idx else " "
        bold = abs_i == idx
        name_sty = "bold cyan" if bold else "cyan"
        msg = r.get("last_msg", "") or ""
        if len(msg) > 50:
            msg = msg[:49] + "…"
        msg_sty = "bold" if bold else "dim"
        ts = r.get("last_ts", "")
        t.add_row(
            f"[bold]{cursor}[/bold]" if bold else cursor,
            f"[{name_sty}]{r['name']}[/{name_sty}]",
            f"[{msg_sty}]{msg}[/{msg_sty}]",
            f"[dim]{ts}[/dim]",
        )

    total = len(rooms)
    if total > visible:
        scroll_info = f"[dim] {scroll + 1}–{min(scroll + visible, total)} of {total} [/dim]"
    else:
        scroll_info = ""

    if mode == "select":
        hint = f"[dim]↑↓ navigate  ·  Enter select  ·  Tab rename  ·  Esc cancel[/dim]{scroll_info}"
    else:
        selected = rooms[idx]["name"] if rooms else ""
        hint = (
            f"[dim]Rename [/dim][cyan]{selected}[/cyan][dim] → [/dim]"
            f"[bold white]{rename_buf}[/bold white][bold cyan]█[/bold cyan]\n"
            "[dim]Enter confirm  ·  Esc/Tab cancel rename[/dim]"
        )

    from rich.console import Group as RGroup
    panel = Panel(
        RGroup(tab_bar, Text(""), t, Text(""), Text.from_markup(hint)),
        title="[bold]Chat Rooms[/bold]",
        border_style="cyan",
        box=box.ROUNDED,
    )
    tmp.print(panel)
    rendered = buf.getvalue()
    return rendered, rendered.count("\n")


def _render_models(models: list[dict], idx: int, current: str,
                   scroll: int, visible: int) -> tuple[str, int]:
    buf = io.StringIO()
    w = Console().width or 100
    tmp = Console(file=buf, highlight=False, markup=True, width=w)

    t = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    t.add_column("", width=2, no_wrap=True)
    t.add_column("Provider", style="dim", min_width=8, no_wrap=True)
    t.add_column("Model", min_width=20, no_wrap=True)
    t.add_column("Size", style="dim", min_width=8, no_wrap=True)
    t.add_column("Params", style="dim", min_width=4, no_wrap=True)
    t.add_column("Quant", style="dim", min_width=8, no_wrap=True)
    t.add_column("", width=8, no_wrap=True)

    window = models[scroll:scroll + visible]
    for i, m in enumerate(window):
        abs_i = scroll + i
        cursor = "▶" if abs_i == idx else " "
        bold = abs_i == idx
        name_sty = "bold cyan" if bold else "cyan"
        active = "[dim](active)[/dim]" if m["id"] == current else ""
        t.add_row(
            f"[bold]{cursor}[/bold]" if bold else cursor,
            m["provider"],
            f"[{name_sty}]{m['name']}[/{name_sty}]",
            m["size"],
            f"[dim]{m.get('params', '')}[/dim]",
            f"[dim]{m.get('quant', '')}[/dim]",
            active,
        )

    total = len(models)
    scroll_info = f"[dim] {scroll + 1}–{min(scroll + visible, total)} of {total} [/dim]" if total > visible else ""
    hint = f"[dim]↑↓ navigate  ·  Enter select  ·  Esc cancel[/dim]{scroll_info}"

    from rich.console import Group as RGroup
    panel = Panel(
        RGroup(t, Text(""), Text.from_markup(hint)),
        title="[bold]Select Model[/bold]",
        border_style="cyan",
        box=box.ROUNDED,
    )
    tmp.print(panel)
    rendered = buf.getvalue()
    return rendered, rendered.count("\n")


def _erase_up(n: int) -> None:
    if n > 0:
        sys.stdout.write(f"\033[{n}A\033[J")
        sys.stdout.flush()

def _enter_altscreen() -> None:
    sys.stdout.write("\033[?1049h\033[H")
    sys.stdout.flush()

def _exit_altscreen() -> None:
    sys.stdout.write("\033[?1049l")
    sys.stdout.flush()

def _move_top() -> None:
    sys.stdout.write("\033[H")
    sys.stdout.flush()


@dataclass
class PickResult:
    action: str          # "select" | "rename" | "cancel"
    name: str = ""
    new_name: str = ""


def pick_room(room_list: list[dict], current: str, console: Console) -> PickResult:
    if not room_list:
        return PickResult("cancel")

    # Reserve lines for panel chrome (title, tabs, hint, borders) ≈ 7
    CHROME = 7
    visible = max(3, _terminal_height() - CHROME)

    idx = next((i for i, r in enumerate(room_list) if r["name"] == current), 0)
    scroll = max(0, min(idx - visible // 2, len(room_list) - visible))
    mode = "select"
    rename_buf = ""
    pending_rename: list[tuple[str, str]] = []

    _enter_altscreen()
    try:
        rendered, _ = _render_rooms(room_list, idx, mode, rename_buf, scroll, visible)
        sys.stdout.write(rendered)
        sys.stdout.flush()

        while True:
            key = _read_key()

            if key == "ignore":
                continue

            if mode == "select":
                if key == "up":
                    idx = (idx - 1) % len(room_list)
                    if idx < scroll:
                        scroll = idx
                    elif idx == len(room_list) - 1:
                        scroll = max(0, len(room_list) - visible)
                elif key == "down":
                    idx = (idx + 1) % len(room_list)
                    if idx >= scroll + visible:
                        scroll = idx - visible + 1
                    elif idx == 0:
                        scroll = 0
                elif key in ("\r", "\n"):
                    _exit_altscreen()
                    return PickResult("select", room_list[idx]["name"])
                elif key == "tab":
                    mode = "rename"
                    rename_buf = ""
                elif key in ("esc", "q"):
                    _exit_altscreen()
                    for old, new in pending_rename:
                        if old == current:
                            return PickResult("rename", old, new)
                    return PickResult("cancel")
            else:
                if key == "esc":
                    mode = "select"
                    rename_buf = ""
                elif key in ("\r", "\n"):
                    if rename_buf.strip():
                        from memory import rooms as _rooms
                        import re as _re
                        new_slug = _re.sub(r"[^\w\-]", "_", rename_buf.strip()).strip("_") or "room"
                        old_name = room_list[idx]["name"]
                        if _rooms.rename_room(old_name, new_slug):
                            room_list[idx]["name"] = new_slug
                            pending_rename.append((old_name, new_slug))
                    mode = "select"
                    rename_buf = ""
                elif key == "backspace":
                    rename_buf = rename_buf[:-1]
                elif key == "tab":
                    mode = "select"
                    rename_buf = ""
                elif len(key) == 1 and key.isprintable():
                    rename_buf += key

            _move_top()
            rendered, _ = _render_rooms(room_list, idx, mode, rename_buf, scroll, visible)
            sys.stdout.write(rendered)
            sys.stdout.flush()
    except Exception:
        _exit_altscreen()
        raise


def _fetch_ollama_models(base_url: str) -> list[dict]:
    try:
        url = base_url.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read())
        models = []
        for m in data.get("models", []):
            details = m.get("details", {})
            models.append({
                "provider": "ollama",
                "name": m["name"],
                "size": _fmt_size(m.get("size", 0)),
                "params": details.get("parameter_size", ""),
                "quant": details.get("quantization_level", ""),
                "id": f"ollama/{m['name']}",
            })
        return models
    except Exception:
        return []


def _fmt_size(b: int) -> str:
    if b == 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def pick_model(current: str, ollama_base_url: str = "http://localhost:11434") -> str | None:
    models = _fetch_ollama_models(ollama_base_url)

    if not models:
        return None

    CHROME = 5
    visible = max(3, _terminal_height() - CHROME)

    idx = next((i for i, m in enumerate(models) if m["id"] == current), 0)
    scroll = max(0, min(idx - visible // 2, len(models) - visible))

    _enter_altscreen()
    try:
        rendered, _ = _render_models(models, idx, current, scroll, visible)
        sys.stdout.write(rendered)
        sys.stdout.flush()

        while True:
            key = _read_key()
            if key == "up":
                idx = (idx - 1) % len(models)
                if idx < scroll:
                    scroll = idx
                elif idx == len(models) - 1:
                    scroll = max(0, len(models) - visible)
            elif key == "down":
                idx = (idx + 1) % len(models)
                if idx >= scroll + visible:
                    scroll = idx - visible + 1
                elif idx == 0:
                    scroll = 0
            elif key in ("\r", "\n"):
                _exit_altscreen()
                return models[idx]["id"]
            elif key in ("esc", "q"):
                _exit_altscreen()
                return None
            elif key == "ignore":
                continue
            _move_top()
            rendered, _ = _render_models(models, idx, current, scroll, visible)
            sys.stdout.write(rendered)
            sys.stdout.flush()
    except Exception:
        _exit_altscreen()
        raise


def pick_permission(dangerous: bool, prefix: str) -> tuple[str, str]:
    """Interactive permission prompt rendered inline below the request panel.

    Returns (action, note) where action is 'once', 'always', or 'deny'.
    Up/Down move, Enter selects, Tab adds a free-text note to the focused
    option, Esc denies. The 'always' option is omitted for dangerous commands.
    """
    from rich.markup import escape
    console = Console()
    short = prefix if len(prefix) <= 40 else prefix[:37] + "..."
    if dangerous:
        options = [("once", "Yes"), ("deny", "No")]
    else:
        options = [
            ("once", "Yes"),
            ("always", f"Yes, and don't ask again for `{escape(short)}` here"),
            ("deny", "No"),
        ]
    idx = 0
    note = ""
    note_mode = False

    def render() -> int:
        lines = []
        for i, (_, label) in enumerate(options):
            if i == idx:
                lines.append(f" [cyan]▶[/cyan] [bold]{label}[/bold]")
            else:
                lines.append(f"   [dim]{label}[/dim]")
        if note_mode:
            lines.append(f"   [yellow]note:[/yellow] {escape(note[-50:])}█")
            lines.append("   [dim]Enter confirm  ·  Esc cancel note[/dim]")
        else:
            lines.append("   [dim]↑↓ select  ·  Enter confirm  ·  Tab add note[/dim]")
        for ln in lines:
            console.print(ln, highlight=False)
        return len(lines)

    n = render()
    while True:
        key = _read_key()
        if note_mode:
            if key in ("\r", "\n"):
                return options[idx][0], note.strip()
            elif key == "esc":
                note_mode = False
                note = ""
            elif key == "backspace":
                note = note[:-1]
            elif key in ("up", "down", "tab", "ignore"):
                pass
            elif len(key) == 1 and key.isprintable():
                note += key
        else:
            if key == "up":
                idx = (idx - 1) % len(options)
            elif key == "down":
                idx = (idx + 1) % len(options)
            elif key in ("\r", "\n"):
                return options[idx][0], note.strip()
            elif key == "tab":
                note_mode = True
            elif key == "esc":
                return "deny", ""
        sys.stdout.write(f"\033[{n}A\033[J")
        sys.stdout.flush()
        n = render()
