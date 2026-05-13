import subprocess
from tools.base import BaseTool, register_tool


def _run(cmd: str, cwd: str | None = None) -> str:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, cwd=cwd)
        out = result.stdout.rstrip()
        err = result.stderr.rstrip()
        if result.returncode != 0 and err:
            return f"[exit {result.returncode}]\n{err}"
        return out or f"(exit {result.returncode})"
    except subprocess.TimeoutExpired:
        return "Error: git command timed out"
    except Exception as e:
        return f"Error: {e}"


@register_tool
class GitStatus(BaseTool):
    name = "git_status"
    description = "Show the working tree status (staged, unstaged, untracked files)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repository path (default: current directory)"},
        },
        "required": [],
    }

    def execute(self, path: str = ".") -> str:
        return _run("git status --short --branch", cwd=path)


@register_tool
class GitDiff(BaseTool):
    name = "git_diff"
    description = "Show changes between commits, working tree, or staged files."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repository path (default: current directory)"},
            "staged": {"type": "boolean", "description": "Show staged changes (default: false = unstaged)"},
            "file": {"type": "string", "description": "Limit diff to a specific file"},
        },
        "required": [],
    }

    def execute(self, path: str = ".", staged: bool = False, file: str | None = None) -> str:
        args = "git diff"
        if staged:
            args += " --cached"
        if file:
            args += f" -- {file}"
        return _run(args, cwd=path)


@register_tool
class GitLog(BaseTool):
    name = "git_log"
    description = "Show recent commit history."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repository path (default: current directory)"},
            "n": {"type": "integer", "description": "Number of commits to show (default: 10)"},
            "oneline": {"type": "boolean", "description": "Compact one-line format (default: true)"},
        },
        "required": [],
    }

    def execute(self, path: str = ".", n: int = 10, oneline: bool = True) -> str:
        fmt = "--oneline" if oneline else "--format='%h %as %an: %s'"
        return _run(f"git log {fmt} -n {n}", cwd=path)


@register_tool
class GitCommit(BaseTool):
    name = "git_commit"
    description = "Stage all changes and create a commit with the given message."
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message"},
            "path": {"type": "string", "description": "Repository path (default: current directory)"},
            "add_all": {"type": "boolean", "description": "Stage all changes before committing (default: true)"},
        },
        "required": ["message"],
    }

    def execute(self, message: str, path: str = ".", add_all: bool = True) -> str:
        if add_all:
            add_result = _run("git add -A", cwd=path)
            if "Error" in add_result or "error" in add_result.lower():
                return f"git add failed:\n{add_result}"
        return _run(f'git commit -m {message!r}', cwd=path)
