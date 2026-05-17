import os
from abc import ABC, abstractmethod
from collections import namedtuple
from typing import Any

_REGISTRY: dict[str, "BaseTool"] = {}

# A permission callback returns one of these. action ∈ {"once", "always", "deny"}.
PermissionDecision = namedtuple("PermissionDecision", ["action", "note"])

_permission_mode: str = "auto"
_permission_callback = None  # fn(tool_name, args, preview, dangerous) -> PermissionDecision
_pending_note: str = ""      # note from the last permission prompt, for the agent loop


def set_permission_callback(fn) -> None:
    global _permission_callback
    _permission_callback = fn


def get_permission_mode() -> str:
    return _permission_mode


def set_permission_mode(mode: str) -> None:
    global _permission_mode
    _permission_mode = mode


def consume_permission_note() -> str:
    """Return the note from the last permission prompt and clear it."""
    global _pending_note
    note = _pending_note
    _pending_note = ""
    return note


def request_permission(tool_name: str, args: dict, preview: str, dangerous: bool = False) -> bool:
    """Returns True if the tool call is allowed to proceed."""
    global _pending_note
    from tools.permissions import matches, add_rule
    cwd = os.getcwd()
    # auto mode: only dangerous calls need a prompt; everything else is allowed
    if _permission_mode != "manual" and not dangerous:
        return True
    # persistent allowlist (never applies to dangerous commands)
    if not dangerous and matches(cwd, tool_name, args):
        return True
    if not _permission_callback:
        return not dangerous  # no UI: allow safe calls, block dangerous ones
    decision = _permission_callback(tool_name, args, preview, dangerous)
    _pending_note = decision.note or ""
    if decision.action == "always" and not dangerous:
        add_rule(cwd, tool_name, args)
    return decision.action in ("once", "always")

_GROUP_MAP: dict[str, list[str]] = {
    "file_ops": ["read_file", "write_file", "append_file", "edit_file", "list_dir"],
    "bash": ["bash"],
    "web": ["web_fetch", "web_search"],
    "git": ["git_status", "git_diff", "git_log", "git_commit"],
}


def register_tool(cls):
    instance = cls()
    _REGISTRY[instance.name] = instance
    return cls


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments

    @abstractmethod
    def execute(self, **kwargs) -> str:
        ...

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def get_all_tools() -> dict[str, BaseTool]:
    return _REGISTRY


def get_tool_schemas(enabled: list[str] | None = None) -> list[dict]:
    if enabled is None:
        return [t.to_schema() for t in _REGISTRY.values()]
    allowed = set()
    for group in enabled:
        allowed.update(_GROUP_MAP.get(group, [group]))
    # Tools not belonging to any built-in group (ask_user, MCP tools) were
    # loaded deliberately and are always exposed; group filtering only gates
    # the built-in grouped tools.
    grouped = {name for names in _GROUP_MAP.values() for name in names}
    return [
        t.to_schema() for name, t in _REGISTRY.items()
        if name in allowed or name not in grouped
    ]


def dispatch_tool(name: str, args: dict) -> str:
    tool = _REGISTRY.get(name)
    if not tool:
        return f"Error: unknown tool '{name}'"
    try:
        # Sanitize path args that may contain annotation noise (e.g. '[file: "foo.txt"')
        if "path" in args and isinstance(args["path"], str):
            import re as _re
            m = _re.search(r'"([^"]+\.\w+)"', args["path"])
            if m and args["path"] != m.group(1):
                args = {**args, "path": m.group(1)}
        return tool.execute(**args)
    except Exception as e:
        return f"Error running {name}: {e}"


def load_tools(enabled: list[str], config: dict | None = None) -> None:
    module_map = {
        "file_ops": "tools.file_ops",
        "bash": "tools.bash",
        "web": "tools.web",
        "git": "tools.git",
    }
    import importlib
    for group in enabled:
        mod = module_map.get(group)
        if mod:
            importlib.import_module(mod)
    if enabled:
        importlib.import_module("tools.interaction")  # ask_user is always available
    if config and config.get("mcp_servers"):
        from tools.mcp import load_mcp_servers
        load_mcp_servers(config)
