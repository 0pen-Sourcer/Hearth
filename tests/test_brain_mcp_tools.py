"""An agent brain reaches all of Hearth's tools through its MCP server.

Hearth's MCP server lists only the everyday tools, to save the host's room.
Hearth itself reaches the rest with load_tools, but an agent brain (Claude
Code, Codex, OpenCode...) had no way to, so 68 tools were out of its reach.
The server is started here the way Hearth starts it for a brain, and asked.

    .\\.venv\\Scripts\\python.exe tests\\test_brain_mcp_tools.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-brainmcp-")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ZIP = os.path.join(WS, "bundle.zip")
with zipfile.ZipFile(ZIP, "w") as z:
    z.writestr("inside/readme.txt", "hello")


def server(brain: bool):
    env = dict(os.environ, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS, PYTHONIOENCODING="utf-8",
               HEARTH_NO_ACTIVITY_LOG="1")
    for k in ("HEARTH_BRAIN_URL", "HEARTH_BRAIN_TOKEN", "HEARTH_MCP_ALL_TOOLS", "HEARTH_BRAIN_GATE"):
        env.pop(k, None)
    if brain:
        env.update(HEARTH_BRAIN_URL="http://127.0.0.1:9", HEARTH_BRAIN_TOKEN="t")
    return subprocess.Popen([sys.executable, os.path.join(ROOT, "hearth", "mcp_server.py")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, text=True, encoding="utf-8")


def rpc(p, n, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    while True:
        m = json.loads(p.stdout.readline())
        if m.get("id") == n:
            return m.get("result") or {}


def start(p):
    rpc(p, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "test", "version": "1"}})
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    p.stdin.flush()
    return {t["name"] for t in rpc(p, 2, "tools/list")["tools"]}


def text(result):
    return "".join(c.get("text", "") for c in result.get("content") or [])


p = server(brain=True)
try:
    names = start(p)
    check(f"an agent brain is offered find_tools and use_tool beside the everyday tools ({len(names)})",
          {"find_tools", "use_tool", "spawn_subagent", "read_file"} <= names and "list_archive" not in names)
    found = text(rpc(p, 3, "tools/call", {"name": "find_tools", "arguments": {"query": "archive"}}))
    check("find_tools names a tool the list left out, with its parameters",
          "list_archive" in found and "parameters" in found)
    ran = text(rpc(p, 4, "tools/call", {"name": "use_tool",
                                         "arguments": {"name": "list_archive", "arguments": {"path": ZIP}}}))
    check(f"use_tool runs it {ran[:80]!r}", "inside/readme.txt" in ran)
    refused = text(rpc(p, 5, "tools/call", {"name": "use_tool", "arguments": {"name": "ask_user", "arguments": {}}}))
    check("a tool that needs Hearth's own window is still not reachable", refused.startswith("Error"))
finally:
    p.kill()

p = server(brain=False)
try:
    check("another host (LM Studio, Claude Desktop) doesn't get them", "find_tools" not in start(p))
finally:
    p.kill()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all brain MCP tool checks passed")
