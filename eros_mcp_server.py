"""Expose eros's built-in tools as an MCP server over stdio.

Run with: eros-mcp-server  (requires the 'mcp' extra)

Other MCP clients (Claude Desktop, another eros instance, etc.) can connect
and call eros's file/bash/git/web tools.
"""
import asyncio
import contextlib
import sys


def _load() -> None:
    from config import load_config
    from tools.base import load_tools
    config = load_config()
    # config=None: do not re-load nested external MCP servers (avoids
    # subprocess sprawl and keeps stdout clean for the JSON-RPC channel).
    load_tools(config.get("tools_enabled", []), config=None)


async def _list_tools():
    import mcp.types as types
    from tools.base import get_all_tools
    return [
        types.Tool(name=t.name, description=t.description, inputSchema=t.parameters)
        for name, t in get_all_tools().items()
        if name != "ask_user"  # no interactive user behind an MCP server
    ]


async def _call_tool(name: str, arguments: dict):
    import mcp.types as types
    from tools.base import dispatch_tool
    result = await asyncio.to_thread(dispatch_tool, name, arguments)
    return [types.TextContent(type="text", text=result)]


def build_server():
    from mcp.server.lowlevel import Server
    server = Server(name="eros", version="0.1.0")
    server.list_tools()(_list_tools)
    server.call_tool()(_call_tool)
    return server


async def _run() -> None:
    from mcp.server.stdio import stdio_server
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("eros-mcp-server requires the 'mcp' extra: uv pip install -e '.[mcp]'",
              file=sys.stderr)
        sys.exit(1)
    # Tool loading may print ("[mcp] ..."); stdout is the JSON-RPC channel,
    # so redirect any load-time output to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        _load()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
