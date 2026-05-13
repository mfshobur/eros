import os
from pathlib import Path
from tools.base import BaseTool, register_tool, request_permission


@register_tool
class ReadFile(BaseTool):
    name = "read_file"
    description = (
        "Read the contents of a file. Returns numbered lines. "
        "For large files use start_line/end_line to read in chunks (max 500 lines per call)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file"},
            "start_line": {"type": "integer", "description": "First line to read (1-indexed, default: 1)"},
            "end_line": {"type": "integer", "description": "Last line to read inclusive (default: start_line + 499)"},
        },
        "required": ["path"],
    }

    MAX_LINES = 500

    def execute(self, path: str, start_line: int | str | None = None, end_line: int | str | None = None) -> str:
        if start_line is not None:
            start_line = int(start_line)
        if end_line is not None:
            end_line = int(end_line)
        p = Path(path).expanduser()
        if not p.exists():
            return f"Error: file not found: {path}"
        lines = p.read_text(errors="replace").splitlines(keepends=True)
        total = len(lines)
        s = (start_line or 1) - 1
        e = end_line or (s + self.MAX_LINES)
        e = min(e, s + self.MAX_LINES, total)
        chunk = lines[s:e]
        numbered = "".join(f"{s+i+1:4d} | {l}" for i, l in enumerate(chunk))
        suffix = f"\n[{total - e} more lines; call again with start_line={e+1}]" if e < total else ""
        return (numbered or "(empty file)") + suffix


@register_tool
class WriteFile(BaseTool):
    name = "write_file"
    description = "Write content to a file, creating it or overwriting it entirely."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "content": {"type": "string", "description": "Full content to write"},
        },
        "required": ["path", "content"],
    }

    def execute(self, path: str, content: str = "") -> str:
        if not content:
            return (
                "Error: content is required. Use the STARTCONTENT/ENDCONTENT format:\n"
                '{"name": "write_file", "arguments": {"path": "' + path + '"}}\n'
                "STARTCONTENT\n<file content here>\nENDCONTENT"
            )
        lines = content.splitlines()
        preview = "\n".join(lines[:8]) + ("\n…" if len(lines) > 8 else "")
        if not request_permission("write_file", {"path": path}, preview):
            return "Cancelled by user."
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"


@register_tool
class EditFile(BaseTool):
    name = "edit_file"
    description = (
        "Replace text inside a file. "
        "old_string must match exactly (including whitespace). "
        "Use replace_all=true to replace every occurrence; default replaces only the first. "
        "Fails if old_string is not found."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "old_string": {"type": "string", "description": "Exact text to find and replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def execute(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        p = Path(path).expanduser()
        if not p.exists():
            return f"Error: file not found: {path}"
        content = p.read_text()
        count = content.count(old_string)
        if count == 0:
            return "Error: old_string not found in file"
        if count > 1 and not replace_all:
            return f"Error: old_string appears {count} times; use replace_all=true or be more specific"

        before = content[: content.index(old_string)]
        start_line = before.count("\n") + 1
        replacements = count if replace_all else 1

        removed = "\n".join(f"- {l}" for l in old_string.splitlines())
        added = "\n".join(f"+ {l}" for l in new_string.splitlines())
        preview = f"{path}:{start_line}\n{removed}\n{added}"
        if not request_permission("edit_file", {"path": path}, preview):
            return "Cancelled by user."

        new_content = content.replace(old_string, new_string, replacements)
        p.write_text(new_content)

        removed = old_string.splitlines()
        added = new_string.splitlines()
        return (
            f"EDIT_DIFF\npath={path}\nstart_line={start_line}\nreplacements={replacements}\n"
            f"removed={len(removed)}\nadded={len(added)}\n"
            + "\n".join(f"-{l}" for l in removed)
            + ("\n" if removed else "")
            + "\n".join(f"+{l}" for l in added)
        )


@register_tool
class AppendFile(BaseTool):
    name = "append_file"
    description = "Append content to a file without overwriting it. Use this to write a file in multiple chunks."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "content": {"type": "string", "description": "Content to append"},
        },
        "required": ["path", "content"],
    }

    def execute(self, path: str, content: str = "") -> str:
        if not content:
            return (
                "Error: content is required. Use the STARTCONTENT/ENDCONTENT format:\n"
                '{"name": "append_file", "arguments": {"path": "' + path + '"}}\n'
                "STARTCONTENT\n<content to append>\nENDCONTENT"
            )
        lines = content.splitlines()
        preview = "\n".join(lines[:8]) + ("\n…" if len(lines) > 8 else "")
        if not request_permission("append_file", {"path": path}, preview):
            return "Cancelled by user."
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(content)
        return f"Appended {len(content)} bytes to {path}"


@register_tool
class ListDir(BaseTool):
    name = "list_dir"
    description = "List files and directories at a path. Shows a tree view."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (default: current directory)"},
            "depth": {"type": "integer", "description": "How deep to recurse (default: 2)"},
        },
        "required": [],
    }

    def execute(self, path: str = ".", depth: int = 2) -> str:
        root = Path(path).expanduser()
        if not root.exists():
            return f"Error: path not found: {path}"
        lines = [str(root)]
        self._walk(root, "", depth, lines)
        return "\n".join(lines)

    def _walk(self, directory: Path, prefix: str, depth: int, lines: list) -> None:
        if depth == 0:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name))
        except PermissionError:
            return
        for i, entry in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            size_hint = ""
            if entry.is_file():
                try:
                    s = entry.stat().st_size
                    size_hint = f"  ({s:,} B)" if s < 1024 else f"  ({s // 1024} KB)"
                except OSError:
                    pass
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else size_hint}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                self._walk(entry, prefix + extension, depth - 1, lines)
