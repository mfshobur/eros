# Contributing to eros

## Adding a new tool

Each tool is a Python class in `tools/`. Here's the minimal template:

```python
# tools/my_tool.py
from tools.base import BaseTool, register_tool

@register_tool
class MyTool(BaseTool):
    name = "my_tool"
    description = "One sentence describing what this tool does."
    parameters = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "What the argument is for."},
        },
        "required": ["input"],
    }

    def execute(self, input: str) -> str:
        return f"result: {input}"
```

Then:

1. Add your tool name to `_GROUP_MAP` in `tools/base.py` under an existing group or a new one:
   ```python
   "my_group": ["my_tool"],
   ```
2. Add the group name to `tools_enabled` in `config.yaml`:
   ```yaml
   tools_enabled:
     - my_group
   ```
3. Write a test in `tests/` (see `tests/test_file_ops.py` for examples).

## Permission handling

If your tool writes to disk or runs external commands, call `request_permission` before executing:

```python
from tools.base import request_permission

def execute(self, path: str, content: str) -> str:
    if not request_permission(self.name, {"path": path}, f"write → {path}"):
        return "Cancelled by user."
    # ... do the write
```

This respects `auto` vs `manual` mode without any extra wiring.

## Guidelines

- `execute()` must return a plain string. The agent injects it back into the conversation as a tool result.
- Keep tool descriptions short and specific. The model reads them on every request.
- Don't raise exceptions from `execute()`; return an `"Error: ..."` string instead so the agent can recover.
- Run `pytest tests/ -v` before opening a PR.
