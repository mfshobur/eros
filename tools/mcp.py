"""MCP (Model Context Protocol) client — bridges MCP servers into eros's tool registry."""
import asyncio
import threading

from tools.base import BaseTool, _REGISTRY


class MCPServer:
    """Manages a single MCP server subprocess and its async session."""

    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None):
        self.name = name
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name=f"mcp-{name}")
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._connect(command, args, env), self._loop)
        future.result(timeout=15)

    async def _connect(self, command: str, args: list[str], env: dict | None) -> None:
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp import ClientSession
        params = StdioServerParameters(command=command, args=args, env=env)
        self._cm = stdio_client(params)
        self._read, self._write = await self._cm.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

    def list_tools(self) -> list:
        f = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop)
        return f.result(timeout=10).tools

    def call_tool(self, name: str, arguments: dict) -> str:
        f = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        )
        result = f.result(timeout=30)
        parts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(parts) or "(no output)"


class MCPTool(BaseTool):
    def __init__(self, server: MCPServer, tool_info) -> None:
        self.name = tool_info.name
        self.description = tool_info.description or ""
        self.parameters = tool_info.inputSchema or {"type": "object", "properties": {}}
        self._server = server

    def execute(self, **kwargs) -> str:
        return self._server.call_tool(self.name, kwargs)


def load_mcp_servers(config: dict) -> None:
    servers_cfg = config.get("mcp_servers", [])
    if not servers_cfg:
        return
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("[mcp] 'mcp' package not installed. Run: uv pip install 'eros[mcp]'")
        return

    for srv_cfg in servers_cfg:
        srv_name = srv_cfg.get("name", "?")
        try:
            server = MCPServer(
                name=srv_name,
                command=srv_cfg["command"],
                args=srv_cfg.get("args", []),
                env=srv_cfg.get("env"),
            )
            tools = server.list_tools()
            for tool_info in tools:
                _REGISTRY[tool_info.name] = MCPTool(server, tool_info)
            print(f"[mcp] {srv_name}: {len(tools)} tool(s) loaded")
        except Exception as e:
            print(f"[mcp] failed to load '{srv_name}': {e}")
