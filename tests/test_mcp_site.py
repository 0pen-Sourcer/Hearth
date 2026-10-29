"""Which site's icon an MCP server row shows, worked out from the server
itself: a URL server's host, a hosted service's package name, a folder for a
file server, nothing for a local helper.

    .\\.venv\\Scripts\\python.exe tests\\test_mcp_site.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import mcp_client  # noqa: E402

site = getattr(mcp_client, "server_site", None)
check("mcp_client works out a server's site", callable(site))
if callable(site):
    key = {"env": {"TOKEN": "x"}}
    cases = [
        ({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], **key}, ("github.com", "")),
        ({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack@1.2.0"], **key}, ("slack.com", "")),
        ({"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"], **key}, ("notion.com", "")),
        ({"command": "docker", "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
                                        "ghcr.io/github/github-mcp-server"], **key}, ("github.com", "")),
        ({"command": "npx", "args": ["-y", "mcp-remote", "https://mcp.linear.app/sse"]}, ("linear.app", "")),
        ({"url": "https://mcp.notion.com/mcp"}, ("notion.com", "")),
        ({"url": "https://api.example.co.uk/mcp"}, ("example.co.uk", "")),
        ({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\me"]}, ("", "folder")),
        # A local helper has no service behind it: no guessed site, no wrong logo.
        ({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}, ("", "")),
        ({"command": "uvx", "args": ["mcp-server-time"]}, ("", "")),
        ({"command": "node", "args": ["C:/tools/mcp/server.js"], **key}, ("", "")),
        ({"command": "python", "args": ["my_tool.py"]}, ("", "")),
        ({}, ("", "")),
    ]
    for spec, want in cases:
        got = site(spec)
        check(f"{spec.get('url') or ' '.join(spec.get('args') or []) or 'empty'} -> {want} ({got})", got == want)

    mcp_client._BRIDGES["gh"] = {"name": "gh", "raw_name": "gh", "site": "github.com", "kind": "", "state": "connected", "tools": []}
    row = [b for b in mcp_client.list_bridges() if b["name"] == "gh"][0]
    check("the status list carries the site", row.get("site") == "github.com" and "kind" in row)
    mcp_client._BRIDGES.pop("gh", None)

print()
print("FAIL" if FAILS else "PASS", f"({len(FAILS)} failed)" if FAILS else "")
sys.exit(1 if FAILS else 0)
