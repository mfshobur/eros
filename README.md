# eros

> A local AI agent that works with the models you actually have.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Ollama](https://img.shields.io/badge/powered%20by-Ollama-black)](https://ollama.com)
[![Tests](https://github.com/mfshobur/eros/actions/workflows/test.yml/badge.svg)](https://github.com/mfshobur/eros/actions/workflows/test.yml)

Eros is a terminal-based AI agent built for small local LLMs. It runs models via Ollama (or any provider via LiteLLM) and takes real actions: reading and editing files, running bash commands, searching the web, and working with git. Designed from the ground up for 3b–14b models where reliability matters.

## Why eros

Most AI coding tools are designed for cloud models with fast inference and reliable tool calling. Small local models (3b–14b) behave differently: they drop context mid-chain, output broken JSON, or ignore tool instructions entirely.

Eros is built specifically for that environment:

- **Script mode**: instead of chaining 5 tool calls and hoping the model keeps state, eros generates a single Python script and runs it. One shot, deterministic, works on any model size.
- **No cloud dependency**: everything runs on your machine. Your code, your files, your conversations stay local.
- **Multi-room history**: persistent conversations organized into rooms, auto-named from context, searchable across sessions.
- **Provider agnostic**: swap Ollama for OpenAI, Anthropic, or Groq by changing one line in config.yaml.
- **Hackable**: adding a new tool is ~15 lines of Python. No framework, no abstractions.

## Quick Start

```bash
# 1. Install
uv venv && uv pip install -e .

# 2. Run
uv run eros

# Or activate the venv first, then just:
source .venv/bin/activate
eros

# Options
eros --model ollama/llama3.2   # override model
eros --room myproject          # start in a specific room
eros --no-tools                # disable all tools
```

## Configuration

Edit `config.yaml`:

```yaml
model: ollama/<name>     # any LiteLLM model string
ollama_base_url: http://localhost:11434
system_prompt: "Answer directly and concisely."
show_thinking: false              # show reasoning tokens while streaming
permission_mode: auto             # auto | manual (see Permissions section)
max_history_turns: 50
max_tokens: 8192                  # max tokens per response
max_tool_iterations: 8

# Per-model overrides (matched by substring against the full model string)
model_defaults:
  qwen3.5: {show_thinking: false, thinking: false}
  qwen3:   {show_thinking: false, thinking: false}
  gemma4:  {show_thinking: false, thinking: false}

tools_enabled:
  - file_ops
  - bash
  - web
  - git
```

**Supported model prefixes:**

| Provider  | Example model string              | Backend     |
|-----------|-----------------------------------|-------------|
| Ollama    | `ollama/llama3.2`                 | direct      |
| Anthropic | `anthropic/claude-sonnet-4-6`     | LiteLLM     |
| OpenAI    | `openai/gpt-4o`                   | LiteLLM     |
| Groq      | `groq/llama-3.1-70b-versatile`    | LiteLLM     |

Set API keys in `config.yaml` under `api_keys:` or as environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`).

## Slash Commands

| Command                   | Description                                                    |
|---------------------------|----------------------------------------------------------------|
| `/model`                  | Interactive model picker, browse Ollama models, ↑↓ navigate   |
| `/model <name>`           | Switch model directly by name                                  |
| `/rooms`                  | Browse chat rooms (↑↓ navigate · Enter select · Tab rename)   |
| `/room-new`               | New room, named automatically from your first message          |
| `/room-delete <name>`     | Delete a room                                                  |
| `/search <query>`         | Search across all rooms by keyword                             |
| `/thinking`               | Toggle reasoning display on/off (Qwen3, DeepSeek, etc.)        |
| `/token-generated`        | Toggle live tok/s display after each response                  |
| `/permissions`            | Toggle manual/auto tool approval mode                          |
| `/tools`                  | List all loaded tools                                          |
| `/system <text>`          | Override system prompt for this session                        |
| `/clear`                  | Clear conversation history (current room)                      |
| `/history`                | Show turns in context + current room name                      |
| `/help`                   | Show all commands                                              |
| `/exit`                   | Quit                                                           |

Type `/` to open the autocomplete menu. Use `↑`/`↓` to navigate, `Tab` or `Enter` to complete. The separator line above the prompt shows the current room and model name. It updates when you switch either.

## Permissions

The agent has two permission modes, similar to Claude Code's "manual approval" feature:

- **`auto`** (default): tool calls run immediately. Only explicitly dangerous bash patterns (`rm`, `dd`, `mkfs`, etc.) prompt for confirmation.
- **`manual`**: every bash command and every file write/edit/append shows a confirmation panel before executing. You see exactly what will run before it does.

Toggle mid-session with `/permissions`, or set `permission_mode: manual` in `config.yaml` to always start in manual mode.

In manual mode, each tool call shows a panel like:

```
╭─ Run command ───────────────────────────────╮
│  python3 -                                  │
╰─────────────────────────────────────────────╯
Allow? [Y/n]

╭─ Edit file  agent.py:42 ────────────────────╮
│  - old_function_name                        │
│  + new_function_name                        │
╰─────────────────────────────────────────────╯
Allow? [Y/n]
```

## Model Picker

`/model` opens an interactive browser of all models available in your Ollama instance, showing name and file size. The currently active model is marked `(active)`. Navigate with `↑`/`↓`, confirm with `Enter`, cancel with `Esc`.

```
╭─ Select Model ──────────────────────────────────────╮
│   ollama   gemma4:e4b          3.3 GB   (active)    │
│ ▶ ollama   qwen3:8b            5.0 GB               │
│   ollama   llama3.2            2.0 GB               │
│                                                      │
│  ↑↓ navigate  ·  Enter select  ·  Esc cancel        │
╰──────────────────────────────────────────────────────╯
```

Per-model defaults (e.g. `show_thinking`) are applied automatically when switching models via `model_defaults` in `config.yaml`.

## Reasoning Models (Qwen3, DeepSeek, etc.)

Models with `<think>...</think>` reasoning are fully supported. Reasoning tokens stream live in a dim block above the response while the model is thinking.

- **Toggle reasoning display**: `/thinking` turns the reasoning block on or off. After each response, the footer shows `thinking: on/off (~N tok)` with the thinking token count.
- **Disable reasoning per message**: append `/no_think` to your message, e.g. `hello /no_think`. The suffix is stripped before display; `think: false` is passed directly to Ollama, cutting response time significantly.
- **Force reasoning per message**: append `/think` to override the global setting for one turn.
- **Per-model defaults**: set `show_thinking` under `model_defaults` in `config.yaml` to configure each model automatically.

## File References

Type `@` followed by a path to attach a file or folder to your message:

- **`@filename.py`**: inlines the file content for the model to read
- **`@src/components/`**: attaches all files in a directory (up to 20 files)
- Files up to 20,000 chars are inlined; larger files get a hint to use `read_file`
- Tab autocomplete lists all files in the current directory tree

## Chat Rooms

Conversations are stored as JSONL files in `~/.local/share/eros/rooms/`. On startup, the last room resumes automatically and the last 5 turns are displayed.

- Rooms **auto-name** from your first message; rename interactively with `/rooms` → Tab
- `/search <query>` searches across all rooms by keyword
- When the estimated token usage exceeds 80% of the model's context window, older turns are compressed into a summary automatically, so context is preserved without hitting token limits
- `⚠ context N%` appears in the status line as you approach the limit

## Tools

| Tool           | Description                                                        |
|----------------|--------------------------------------------------------------------|
| `read_file`    | Read file contents with optional line range                        |
| `write_file`   | Create or overwrite a file                                         |
| `append_file`  | Append content to a file without overwriting                       |
| `edit_file`    | Replace an exact string inside a file (verified after write)       |
| `list_dir`     | Tree view of a directory                                           |
| `bash`         | Run shell commands (with confirmation in manual mode)              |
| `web_fetch`    | Fetch and strip HTML from any URL                                  |
| `web_search`   | DuckDuckGo search (no API key needed)                              |
| `git_status`   | Show working tree status                                           |
| `git_diff`     | Show staged or unstaged diffs                                      |
| `git_log`      | Recent commit history                                              |
| `git_commit`   | Stage all and commit                                               |

### Script Mode

For data-processing and file-editing tasks, the agent automatically switches to **script mode**: it generates a complete Python script using only stdlib (`csv`, `json`, `os`, `collections`) and runs it via `python3 -` stdin with no temp file. Up to 3 auto-retries on script errors.

This is the core reliability mechanism for small models. Instead of hoping a 4b model chains 5 tool calls without dropping state, eros writes one deterministic script and runs it.

### Reliability guards

- **Hallucination detection**: if the model claims a file was changed without calling a tool, eros catches it and forces the actual tool call
- **Edit verification**: after every `edit_file`, the file is re-read to confirm the change applied
- **Auto-save**: if the model pastes a code block instead of calling `write_file`, eros saves it to disk automatically

## Architecture

```
eros/
├── main.py          # CLI entrypoint: REPL loop, rooms, file refs, Markdown output
├── agent.py         # Core agent loop: streaming, thinking, tool dispatch, script mode
├── config.py        # Config loader (project + user-level config.yaml)
├── config.yaml      # Default settings
├── pyproject.toml
├── requirements.txt
│
├── tools/
│   ├── base.py      # BaseTool ABC, @register_tool decorator, dispatcher, permissions
│   ├── file_ops.py  # read_file, write_file, append_file, edit_file, list_dir
│   ├── bash.py      # bash execution with safety confirmation
│   ├── web.py       # web_fetch, web_search
│   └── git.py       # git_status, git_diff, git_log, git_commit
│
├── ui/
│   ├── console.py   # Rich terminal output (panels, Markdown, tool display)
│   ├── input.py     # prompt_toolkit: slash + @file autocomplete, dynamic prompt
│   └── picker.py    # Interactive pickers for rooms and models
│
├── memory/
│   └── rooms.py     # Multi-room chat history (~/.local/share/eros/rooms/)
│
└── tests/
    ├── test_agent.py     # complexity detection, hallucination guard, permissions
    └── test_file_ops.py  # write/read/edit/append/list roundtrips
```

### How the agent loop works

```
User input  (@file/@folder refs expanded → inlined content)
  └─► auto-summarize history if >80% full
        └─► complex task detected? → script mode (generate + run via python3 stdin)
              └─► build messages (system prompt + history + user)
                    └─► POST to LLM via LiteLLM (stream=True)
                          ├─► thinking tokens  → streamed live, token count tracked
                          ├─► text tokens      → streamed live
                          └─► tool call detected?
                                ├── YES → check permissions → dispatch → verify (edit_file) → inject result → loop
                                └── NO  → check for hallucinated file change → render Markdown
```

### Tool calling strategy

- **Ollama**: direct streaming to `/api/chat` via stdlib `urllib`, no LiteLLM overhead. Tools described in the system prompt; model outputs `{"name": "tool", "arguments": {...}}` as plain text, parsed via JSON, heredoc format, or a salvage parser for broken JSON.
- **Anthropic / OpenAI / Groq**: routed through LiteLLM with native API function-calling via the `tools` parameter.

### Adding a new tool

```python
# tools/my_tool.py
from tools.base import BaseTool, register_tool

@register_tool
class MyTool(BaseTool):
    name = "my_tool"
    description = "What it does."
    parameters = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "..."},
        },
        "required": ["input"],
    }

    def execute(self, input: str) -> str:
        return f"result: {input}"
```

Then add `"my_tool"` to `_GROUP_MAP` in `tools/base.py` and the group name to `tools_enabled` in `config.yaml`.
