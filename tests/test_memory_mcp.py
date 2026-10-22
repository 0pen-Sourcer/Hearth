"""Hearth's memory as its own MCP server, for the coding agents on this PC.

Started the way an agent starts it (shared_memory.launch), over stdio: it offers
the memory tools and nothing else, a fact saved through it is in Hearth's own
memory, and it can be read back. A throwaway workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_memory_mcp.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-memmcp-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    print("skipped: mcp is not installed")
    sys.exit(0)

from hearth import shared_memory  # noqa: E402


async def main():
    l = shared_memory.launch()
    env = dict(os.environ, **l["env"])
    params = StdioServerParameters(command=l["command"], args=l["args"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            check(f"it offers the memory tools and nothing else {names}",
                  names == sorted(["memory_recall", "memory_save", "memory_list", "memory_forget",
                                   "search_chats", "read_chat", "whats_going_on"]))
            saved = await s.call_tool("memory_save", {"title": "editor-choice", "type": "user",
                                                      "description": "Uses VS Code with the Vim keys."})
            got = await s.call_tool("memory_recall", {"query": "editor"})
            text = " ".join(c.text for c in got.content if hasattr(c, "text"))
            check(f"a fact saved through it can be recalled {text[:90]!r}", "Vim" in text)
    from hearth import memory
    check("and it's in Hearth's own memory", any("editor" in str(t).lower() for t in memory.fact_titles()))

asyncio.run(main())

# The switches, without touching this PC's real agent settings.
import json  # noqa: E402
calls = []
shared_memory._exe = lambda agent: [f"{agent}.exe"]
shared_memory._run = lambda argv, timeout=60: (calls.append(argv) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
shared_memory.set_shared("claude-code", True)
add = [c for c in calls if c[1:3] == ["mcp", "add"]][0]
check(f"Claude Code gets the name before its -e list (its -e would swallow it) {add[:6]}",
      add[3:6] == ["-s", "user", "hearth-memory"] and add.index("hearth-memory") < add.index("-e"))
home = tempfile.mkdtemp(prefix="hearth-home-")
os.environ["USERPROFILE"] = home
os.environ["HOME"] = home
for agent, path, box in (("cursor-agent", os.path.join(home, ".cursor", "mcp.json"), "mcpServers"),
                         ("opencode", os.path.join(home, ".config", "opencode", "opencode.json"), "mcp")):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({box: {"other": {"command": "x"}}, "theme": "dark"}, f)
    shared_memory.set_shared(agent, True)
    on = json.load(open(path, encoding="utf-8"))
    shared_memory.set_shared(agent, False)
    off = json.load(open(path, encoding="utf-8"))
    check(f"{agent}: switched on it's in its config beside what was there, and off it's gone",
          "hearth-memory" in on[box] and "other" in on[box] and on.get("theme") == "dark"
          and "hearth-memory" not in off[box] and "other" in off[box])
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all memory MCP checks passed")
