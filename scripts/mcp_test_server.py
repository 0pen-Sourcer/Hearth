"""Tiny stdio MCP server for testing hearth.mcp_client end-to-end.

Exposes one tool, `echo(text)`, that just bounces the input back. No
external deps beyond the `mcp` lib that Hearth already requires.

Run via mcp.json:
    {"mcpServers": {"echo": {
        "command": "python",
        "args": ["scripts/mcp_test_server.py"]
    }}}
"""
from __future__ import annotations

import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

app = Server("hearth-echo-test")


@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="echo",
            description="Echoes the input text back verbatim. Used to verify Hearth's MCP client wiring.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to echo back."}
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="add",
            description="Adds two integers and returns the sum.",
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "echo":
        return [types.TextContent(type="text",
                                  text=f"echo: {arguments.get('text', '')}")]
    if name == "add":
        a = int(arguments.get("a", 0))
        b = int(arguments.get("b", 0))
        return [types.TextContent(type="text", text=str(a + b))]
    return [types.TextContent(type="text", text=f"unknown tool: {name}")]


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
