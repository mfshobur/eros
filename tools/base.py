from abc import ABC, abstractmethod
from typing import Any

_REGISTRY: dict[str, "BaseTool"] = {}

_permission_mode: str = "auto"
_permission_callback = None  # fn(tool_name, args, preview) -> bool


def set_permission_callback(fn) -> None:
    global _permission_callback
    _permission_callback = fn


def get_permission_mode() -> str:
    return _permission_mode


def set_permission_mode(mode: str) -> None:
    global _permission_mode
    _permission_mode = mode


def request_permission(tool_name: str, args: dict, preview: str) -> bool:
    """Returns True if the tool call is allowed to proceed."""
    if _permission_mode != "manual":
        return True  # auto mode: bypass (bash has its own dangerous-pattern check)
    if _permission_callback:
        return _permission_callback(tool_name, args, preview)
    return True  # no UI registered, allow

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
    return [t.to_schema() for name, t in _REGISTRY.items() if name in allowed]


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


def load_tools(enabled: list[str]) -> None:
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
