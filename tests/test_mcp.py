"""Tests for MCP server support."""
import subprocess
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch

_NPX = "/opt/homebrew/bin/npx" if os.path.exists("/opt/homebrew/bin/npx") else "/usr/local/bin/npx"


# ── MCPTool ───────────────────────────────────────────────────────────────────

class TestMCPTool:
    def _make_tool_info(self, name="list_dir", description="List a directory", schema=None):
        info = MagicMock()
        info.name = name
        info.description = description
        info.inputSchema = schema or {"type": "object", "properties": {"path": {"type": "string"}}}
        return info

    def test_tool_attributes(self):
        from tools.mcp import MCPTool
        server = MagicMock()
        tool = MCPTool(server, self._make_tool_info())
        assert tool.name == "list_dir"
        assert tool.description == "List a directory"
        assert tool.parameters["type"] == "object"

    def test_to_schema_format(self):
        from tools.mcp import MCPTool
        server = MagicMock()
        tool = MCPTool(server, self._make_tool_info())
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "list_dir"
        assert "parameters" in schema["function"]

    def test_execute_delegates_to_server(self):
        from tools.mcp import MCPTool
        server = MagicMock()
        server.call_tool.return_value = "result text"
        tool = MCPTool(server, self._make_tool_info())
        result = tool.execute(path="/tmp")
        server.call_tool.assert_called_once_with("list_dir", {"path": "/tmp"})
        assert result == "result text"

    def test_missing_description_defaults_to_empty(self):
        from tools.mcp import MCPTool
        server = MagicMock()
        info = self._make_tool_info()
        info.description = None
        tool = MCPTool(server, info)
        assert tool.description == ""

    def test_missing_schema_defaults_to_empty_object(self):
        from tools.mcp import MCPTool
        server = MagicMock()
        info = self._make_tool_info()
        info.inputSchema = None
        tool = MCPTool(server, info)
        assert tool.parameters == {"type": "object", "properties": {}}


# ── load_mcp_servers ──────────────────────────────────────────────────────────

class TestLoadMcpServers:
    def test_skips_when_no_mcp_servers(self):
        from tools.mcp import load_mcp_servers
        from tools.base import _REGISTRY
        before = set(_REGISTRY.keys())
        load_mcp_servers({})
        assert set(_REGISTRY.keys()) == before

    def test_graceful_when_mcp_not_installed(self, capsys):
        from tools.mcp import load_mcp_servers
        config = {"mcp_servers": [{"name": "x", "command": "echo", "args": []}]}
        with patch.dict("sys.modules", {"mcp": None}):
            load_mcp_servers(config)
        out = capsys.readouterr().out
        assert "not installed" in out

    def test_prints_error_on_connection_failure(self, capsys):
        from tools.mcp import load_mcp_servers
        config = {"mcp_servers": [{"name": "bad", "command": "nonexistent_cmd_xyz", "args": []}]}
        load_mcp_servers(config)
        out = capsys.readouterr().out
        assert "failed to load" in out


# ── load_tools with mcp_servers ───────────────────────────────────────────────

class TestLoadToolsWithMcp:
    def test_no_mcp_servers_skips_mcp(self):
        from tools.base import load_tools, _REGISTRY
        before = set(_REGISTRY.keys())
        load_tools([], config={})
        assert set(_REGISTRY.keys()) == before

    def test_mcp_servers_key_triggers_load(self, capsys):
        from tools.base import load_tools
        config = {"mcp_servers": [{"name": "bad", "command": "nonexistent_xyz", "args": []}]}
        load_tools([], config=config)
        out = capsys.readouterr().out
        assert "failed to load" in out or "not installed" in out


# ── integration: real filesystem MCP server ───────────────────────────────────

@pytest.mark.skipif(not os.path.exists(_NPX), reason="npx not available")
class TestMCPFilesystemIntegration:
    """Run in a subprocess to avoid pytest stdout-capture breaking MCP's stdio transport."""

    _SCRIPT = """
import sys, tempfile; sys.path.insert(0, '.')
from tools.mcp import load_mcp_servers
from tools.base import _REGISTRY
tmp = tempfile.gettempdir()
import os; tmp = os.path.realpath(tmp)
config = {'mcp_servers': [{'name': 'fs', 'command': 'npx',
    'args': ['-y', '@modelcontextprotocol/server-filesystem', tmp]}]}
load_mcp_servers(config)
tool = _REGISTRY.get('list_directory')
assert tool is not None, 'list_directory not registered'
result = tool.execute(path=tmp)
assert isinstance(result, str) and len(result) > 0, 'empty result'
print('OK:', len(_REGISTRY), 'tools')
"""

    def test_loads_and_calls_filesystem_server(self):
        result = subprocess.run(
            [sys.executable, "-c", self._SCRIPT],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        if result.returncode != 0 and "failed to load" in result.stdout:
            pytest.skip(f"MCP server failed to start (npm/version issue): {result.stderr[-200:]}")
        assert result.returncode == 0, f"stderr: {result.stderr[-500:]}"
        assert "OK:" in result.stdout
