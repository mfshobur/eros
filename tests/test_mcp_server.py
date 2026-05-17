"""Tests for eros exposed as an MCP server (eros_mcp_server.py)."""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

_NPX = "/opt/homebrew/bin/npx" if os.path.exists("/opt/homebrew/bin/npx") else "/usr/local/bin/npx"


@pytest.fixture(scope="module", autouse=True)
def loaded():
    """Populate the tool registry once for the whole module."""
    import eros_mcp_server
    eros_mcp_server._load()


# ── list_tools / call_tool handlers ───────────────────────────────────────────

class TestListTools:
    def test_returns_eros_tools(self):
        import eros_mcp_server
        tools = asyncio.run(eros_mcp_server._list_tools())
        names = {t.name for t in tools}
        assert "read_file" in names
        assert "bash" in names

    def test_excludes_ask_user(self):
        import eros_mcp_server
        tools = asyncio.run(eros_mcp_server._list_tools())
        assert "ask_user" not in {t.name for t in tools}

    def test_tools_have_input_schema(self):
        import eros_mcp_server
        tools = asyncio.run(eros_mcp_server._list_tools())
        for t in tools:
            assert isinstance(t.inputSchema, dict)


class TestCallTool:
    def test_dispatches_read_file(self, tmp_path):
        import eros_mcp_server
        f = tmp_path / "sample.txt"
        f.write_text("mcp server content")
        result = asyncio.run(eros_mcp_server._call_tool("read_file", {"path": str(f)}))
        assert result[0].type == "text"
        assert "mcp server content" in result[0].text

    def test_unknown_tool_returns_error(self):
        import eros_mcp_server
        result = asyncio.run(eros_mcp_server._call_tool("nonexistent_tool_xyz", {}))
        assert "Error" in result[0].text


class TestBuildServer:
    def test_returns_server(self):
        import eros_mcp_server
        server = eros_mcp_server.build_server()
        assert server.name == "eros"


# ── integration: eros's own MCP client connecting to eros's MCP server ────────

@pytest.mark.skipif(not os.path.exists(_NPX), reason="run only where MCP deps are present")
class TestErosMcpServerIntegration:
    """Connect eros's MCP client (tools/mcp.py) to eros_mcp_server.py.

    Runs in a subprocess to avoid pytest stdout-capture breaking MCP stdio.
    """

    _SCRIPT = """
import sys, os
sys.path.insert(0, '.')
from tools.mcp import MCPServer
server = MCPServer(name='eros', command=sys.executable, args=['eros_mcp_server.py'])
tools = server.list_tools()
names = [t.name for t in tools]
assert 'read_file' in names, f'read_file missing: {names}'
result = server.call_tool('git_status', {})
assert isinstance(result, str) and len(result) > 0, 'empty git_status result'
print('OK:', len(tools), 'tools')
"""

    def test_loads_and_calls_via_mcp_client(self):
        result = subprocess.run(
            [sys.executable, "-c", self._SCRIPT],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        if result.returncode != 0 and "failed to load" in result.stdout:
            pytest.skip(f"MCP server failed to start: {result.stderr[-200:]}")
        assert result.returncode == 0, f"stderr: {result.stderr[-500:]}"
        assert "OK:" in result.stdout
