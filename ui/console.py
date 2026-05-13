import json
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich import box

console = Console()


def print_welcome(model: str, tools: list[str]) -> None:
    console.print(
        Panel(
            f"[bold cyan]eros[/bold cyan]  [dim]model:[/dim] [green]{model}[/green]  "
            f"[dim]tools:[/dim] [yellow]{', '.join(tools)}[/yellow]\n"
            "[dim]Type [bold]/help[/bold] for commands, [bold]/exit[/bold] to quit.[/dim]",
            box=box.ROUNDED,
            border_style="dim",
        )
    )


def print_help() -> None:
    console.print(Panel(
        "[bold]Slash commands:[/bold]\n"
        "  [cyan]/model [name][/cyan]          Switch model (e.g. /model ollama/llama3.2)\n"
        "  [cyan]/tools[/cyan]                 List available tools\n"
        "  [cyan]/clear[/cyan]                 Clear conversation history\n"
        "  [cyan]/system [text][/cyan]         Override system prompt\n"
        "  [cyan]/thinking[/cyan]              Toggle thinking output on/off\n"
        "  [cyan]/token-generated[/cyan]       Toggle live tok/s display\n"
        "  [cyan]/permissions[/cyan]           Toggle manual/auto approval for tool calls\n"
        "  [cyan]/history[/cyan]               Show conversation history summary\n"
        "  [cyan]/search <query>[/cyan]        Search across all rooms\n"
        "\n"
        "[bold]Room management:[/bold]\n"
        "  [cyan]/rooms[/cyan]                 Browse rooms (↑↓ · Enter select · Tab rename)\n"
        "  [cyan]/room-new[/cyan]              New room (named from first message)\n"
        "  [cyan]/room-delete [name][/cyan]    Delete a room\n"
        "\n"
        "  [cyan]/exit[/cyan]                  Quit",
        title="Help",
        border_style="cyan",
        box=box.ROUNDED,
    ))


def print_tool_call(name: str, args: dict) -> None:
    if name == "edit_file" and "path" in args:
        filename = args["path"].split("/")[-1]
        console.print(f"  [dim]▶[/dim] [bold yellow]edit_file[/bold yellow]  [dim]{filename}[/dim]")
    else:
        console.print(
            f"  [dim]▶ tool:[/dim] [bold yellow]{name}[/bold yellow]  "
            f"[dim]{_short_args(args)}[/dim]"
        )


_TOOL_PREVIEW_LINES = {
    "bash": 20,
    "read_file": 10,
    "web_search": 8,
    "web_fetch": 8,
}
_DEFAULT_PREVIEW_LINES = 5


def print_tool_result(name: str, result: str) -> None:
    if result.startswith("EDIT_DIFF\n"):
        _print_edit_diff(result)
        return
    max_lines = _TOOL_PREVIEW_LINES.get(name, _DEFAULT_PREVIEW_LINES)
    lines = result.splitlines()
    preview = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        preview += f"\n[dim]... ({len(lines) - max_lines} more lines)[/dim]"
    console.print(Panel(preview, title=f"[dim]{name} result[/dim]", border_style="dim", box=box.SIMPLE))


def _print_edit_diff(result: str) -> None:
    lines = result.splitlines()
    meta: dict = {}
    diff_lines: list[str] = []
    in_diff = False
    for line in lines[1:]:
        if not in_diff and "=" in line and not line.startswith(("+", "-")):
            k, _, v = line.partition("=")
            meta[k] = v
        else:
            in_diff = True
            diff_lines.append(line)

    path = meta.get("path", "?")
    n_added = int(meta.get("added", 0))
    n_removed = int(meta.get("removed", 0))
    start = int(meta.get("start_line", 1))

    filename = path.split("/")[-1]
    console.print(f"  [bold green]●[/bold green] [bold]Update[/bold]([cyan]{filename}[/cyan])")
    console.print(
        f"  [dim]└─ Added [green]{n_added}[/green] line{'s' if n_added != 1 else ''}, "
        f"removed [red]{n_removed}[/red] line{'s' if n_removed != 1 else ''}[/dim]"
    )

    removed_no = start
    added_no = start
    for dl in diff_lines:
        if dl.startswith("-"):
            console.print(f"  [on dark_red]{removed_no:>4} [red]-[/red]  {dl[1:]}[/on dark_red]")
            removed_no += 1
        elif dl.startswith("+"):
            console.print(f"  [on dark_green]{added_no:>4} [green]+[/green]  {dl[1:]}[/on dark_green]")
            added_no += 1


def print_thinking(text: str) -> None:
    console.print(text, style="dim italic", end="")


def print_thinking_panel(text: str) -> None:
    console.print(Panel(text.strip(), title="[dim]💭 thinking[/dim]", border_style="dim", box=box.SIMPLE))


def print_token(text: str) -> None:
    console.print(text, end="", markup=False)


def print_response_end() -> None:
    console.print()  # newline after streaming


def print_error(msg: str) -> None:
    console.print(f"[red]Error:[/red] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")


def confirm(prompt: str, default: bool = False) -> bool:
    console.print(f"\n[yellow]⚠[/yellow]  {prompt}")
    hint = "Y/n" if default else "y/N"
    answer = console.input(f"[bold]Allow? ({hint}): [/bold]").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _short_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:37] + "..."
        parts.append(f"{k}={v_str!r}")
    return ", ".join(parts)
