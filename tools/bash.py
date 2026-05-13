import subprocess
from tools.base import BaseTool, register_tool, request_permission, get_permission_mode

_DANGEROUS_PATTERNS = ["rm ", "rmdir", "dd ", "mkfs", "format", ":(){", "chmod 777", "sudo rm"]

_confirm_callback = None


def set_confirm_callback(fn):
    global _confirm_callback
    _confirm_callback = fn


@register_tool
class BashExec(BaseTool):
    name = "bash"
    description = (
        "Run a shell command and return its stdout + stderr. "
        "Use for running tests, building projects, checking files, etc. "
        "Destructive commands will prompt the user for confirmation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
            "cwd": {"type": "string", "description": "Working directory (default: current)"},
        },
        "required": ["command"],
    }

    _MAX_TIMEOUT = 120

    def execute(self, command: str, timeout: int = 30, cwd: str | None = None) -> str:
        timeout = min(int(timeout), self._MAX_TIMEOUT)
        is_dangerous = any(p in command for p in _DANGEROUS_PATTERNS)
        if get_permission_mode() == "manual":
            if not request_permission("bash", {"command": command}, command):
                return "Cancelled by user."
        elif is_dangerous:
            if _confirm_callback:
                approved = _confirm_callback(f"Run potentially dangerous command?\n  {command}")
                if not approved:
                    return "Cancelled by user."
            else:
                return f"Blocked: command matches dangerous pattern. Run manually if intended:\n  {command}"

        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            out = result.stdout
            err = result.stderr
            parts = []
            if out:
                parts.append(out.rstrip())
            if err:
                parts.append(f"[stderr]\n{err.rstrip()}")
            if result.returncode != 0 and not parts:
                parts.append(f"(exit code {result.returncode})")
            if not parts:
                parts.append("(exit 0, command succeeded)")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"
