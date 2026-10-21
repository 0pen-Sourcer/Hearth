"""The model can read one earlier chat whole, in order.

Only search_chats existed: a handful of matching snippets, which can say a
chat mentioned something but not where it got to, so "read that chat and carry
on" had nothing to carry on from.

    .\\.venv\\Scripts\\python.exe tests\\test_read_chat.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-readchat-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
sys.path.insert(0, ROOT)
os.makedirs(os.path.join(WS, "conversations"), exist_ok=True)


def chat(cid, title, n, updated):
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        m = {"role": role, "content": f"{title} message {i} " + ("x" * 900), "at": 1760000000000 + i * 60000}
        if role == "assistant" and i == n - 1:
            m["events"] = [{"kind": "tool", "name": "write_file"}]
        msgs.append(m)
    with open(os.path.join(WS, "conversations", cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": cid, "title": title, "updated": updated, "messages": msgs}, f)


chat("c_launch", "Launch warroom", 40, 300)
chat("c_phone", "Phone shopping", 6, 200)
chat("c_phone2", "Phone shopping, round two", 4, 250)

from hearth import tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


out = tools.execute_tool("read_chat", {"chat": "launch warroom"})
check("a chat is found by words from its title", out.startswith("Chat: Launch warroom"))
check("it opens with what the chat was for", "Launch warroom message 0 " in out)
check("and ends with where it got to, in full", "Launch warroom message 39 " in out and "[used: write_file]" in out)
pos = [out.find(f"message {i} ") for i in (0, 36, 37, 38, 39)]
check("in order", all(a < b for a, b in zip(pos, pos[1:])) and -1 not in pos)
check(f"what's left out is said, with how to read it ({out.count('left out')})",
      "left out; read_chat with start=1" in out and len(out) < 16000)

more = tools.execute_tool("read_chat", {"chat": "c_launch", "start": 1})
check("by id, from a message on", more.startswith("Chat: Launch warroom") and "#1 assistant" in more
      and "message 0 " not in more and "start=" in more)

two = tools.execute_tool("read_chat", {"chat": "phone shopping round"})
check("the closest title wins when one fits best", two.startswith("Chat: Phone shopping, round two"))
both = tools.execute_tool("read_chat", {"chat": "phone"})
check(f"when several fit as well, they are listed with ids ({both[:60]!r})",
      both.startswith("More than one chat fits") and "id=c_phone" in both and "id=c_phone2" in both)
none = tools.execute_tool("read_chat", {"chat": "volcano trip"})
check("a chat that isn't there says so and where to look", "search_chats" in none)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all read_chat checks passed")
