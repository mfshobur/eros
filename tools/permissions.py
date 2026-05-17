"""Persistent per-directory permission allowlist.

A rule is {"tool": <name>} with an optional "prefix" (bash only). Rules are
stored per working directory in ~/.local/share/eros/permissions.json.
"""
import json
from pathlib import Path

_PERMISSIONS_FILE = Path.home() / ".local" / "share" / "eros" / "permissions.json"


def command_prefix(command: str) -> str:
    """Derive the rule prefix for a bash command: command + subcommand.

    Flag-aware: a second token starting with '-' is treated as an argument,
    not a subcommand. 'git checkout -b x' -> 'git checkout'; 'ls -la' -> 'ls'.
    """
    tokens = command.split()
    if not tokens:
        return command.strip()
    if len(tokens) == 1 or tokens[1].startswith("-"):
        return tokens[0]
    return f"{tokens[0]} {tokens[1]}"


def load_rules() -> dict:
    if not _PERMISSIONS_FILE.exists():
        return {}
    try:
        return json.loads(_PERMISSIONS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_rules(rules: dict) -> None:
    _PERMISSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PERMISSIONS_FILE.write_text(json.dumps(rules, indent=2))


def add_rule(directory: str, tool_name: str, args: dict) -> None:
    """Persist an allow rule for tool_name in directory."""
    rule = {"tool": tool_name}
    if tool_name == "bash":
        rule["prefix"] = command_prefix(args.get("command", ""))
    rules = load_rules()
    dir_rules = rules.setdefault(directory, [])
    if rule not in dir_rules:
        dir_rules.append(rule)
        _save_rules(rules)


def matches(directory: str, tool_name: str, args: dict) -> bool:
    """True if a stored rule covers this tool call in directory."""
    for rule in load_rules().get(directory, []):
        if rule.get("tool") != tool_name:
            continue
        prefix = rule.get("prefix")
        if prefix is None:
            return True  # tool-level rule (file tools): allow any use
        if tool_name == "bash" and command_prefix(args.get("command", "")) == prefix:
            return True
    return False
