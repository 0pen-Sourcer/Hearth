"""What an agent brain did is shown as Hearth's own tools: a call through
use_tool appears as the tool it ran with that tool's arguments, a find_tools
lookup isn't shown, and Codex's web steps appear as a search or as the page it
opened (so the window can show that site's icon).

    .\\.venv\\Scripts\\python.exe tests\\test_agent_tool_view.py
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")

from hearth import claude_brain as B, codex_brain as X  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A turn on an agent brain, through the same wrapper every agent's events pass.
events = [
    ("tool_call_partial", {"name": "use_tool", "args_text": '{"na'}),
    ("tool_call", {"name": "find_tools", "args": {"query": "read chat"}, "id": "a"}),
    ("tool_result", {"name": "find_tools", "content": "read_chat: ...", "id": "a"}),
    ("tool_call", {"name": "use_tool", "id": "b",
                   "args": {"name": "read_chat", "arguments": {"chat": "c_1"}}}),
    ("tool_result", {"name": "use_tool", "content": "the chat", "id": "b"}),
    ("tool_call", {"name": "mcp__hearth_brain__use_tool", "id": "c",
                   "args": {"name": "web_search", "arguments": '{"query": "phones under 30k"}'}}),
    ("tool_result", {"name": "mcp__hearth_brain__use_tool", "content": "results", "id": "c"}),
    ("tool_call", {"name": "memory_recall", "args": {"query": "phone"}, "id": "d"}),
]
shown = []


class Fake:
    @staticmethod
    async def run_turn(prompt, *, emit, **k):
        for kind, fields in events:
            emit(kind, **fields)
        emit("assistant", content="Moto Edge 60 Pro.")
        emit("done", reason="finished")
        return 0


sys.modules["hearth.fake_brain"] = Fake
B.ACP["fake"] = {}
_cur, B.current = B.current, (lambda: "fake")
_imp = __import__("importlib").import_module
__import__("importlib").import_module = lambda n, p=None: Fake if n == ".acp_brain" else _imp(n, p)
try:
    asyncio.run(B.run_turn("do you remember what phone I was about to buy?",
                           emit=lambda kind, **f: shown.append((kind, f))))
finally:
    __import__("importlib").import_module = _imp
    B.current = _cur

calls = [(f.get("name"), f.get("args")) for k, f in shown if k == "tool_call"]
results = [f.get("name") for k, f in shown if k == "tool_result"]
check(f"use_tool shows as the tool it ran, with its arguments ({calls})",
      ("read_chat", {"chat": "c_1"}) in calls and ("web_search", {"query": "phones under 30k"}) in calls)
check("and its result is that tool's", results[:2] == ["read_chat", "web_search"])
check("a find_tools lookup isn't shown", not any(n in ("find_tools", "use_tool") for n, _ in calls)
      and "find_tools" not in results and "use_tool" not in results)
check("nor half-written use_tool calls", not [k for k, f in shown if k == "tool_call_partial"])
check("other tools are untouched", ("memory_recall", {"query": "phone"}) in calls)

# Codex's web steps.
check("a Codex search is a web search for its query",
      X._search_call({"action": {"type": "search", "query": "nord 5 price"}}) == ("web_search", {"query": "nord 5 price"}))
check("with several queries, the first",
      X._search_call({"action": {"type": "search", "queries": ["a", "b"]}}) == ("web_search", {"query": "a"}))
check("a page Codex opened is that page",
      X._search_call({"action": {"type": "openPage", "url": "https://www.gsmarena.com/x"}})
      == ("web_fetch", {"url": "https://www.gsmarena.com/x"}))
check("as is a search inside a page",
      X._search_call({"action": {"type": "findInPage", "url": "https://croma.com/p", "pattern": "price"}})[0] == "web_fetch")
check("an older step without an action keeps its query",
      X._search_call({"query": "selfie"}) == ("web_search", {"query": "selfie"}))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent tool view checks passed")
