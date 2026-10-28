"""The phone rebuilds a chat from the PC's file the way the window does: each
reply's model, time and cost from its turn's done event, its thinking, and its
steps with the sites they reached.

    .\\.venv\\Scripts\\python.exe tests\\test_relay_open.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-open-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import chat_sync, relay_bridge as R  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


folder = chat_sync.CONVOS_DIR
os.makedirs(folder, exist_ok=True)
chat = {"id": "c_test", "title": "Phones", "messages": [
    {"role": "user", "content": "best phone under 30k?", "at": 1},
    {"role": "assistant", "content": "The Nord 5.", "at": 2, "events": [
        {"kind": "think_chunk", "content": "They want ", "pos": 0},
        {"kind": "think_chunk", "content": "a camera phone.", "pos": 0},
        {"kind": "tool", "name": "web_search", "args": {"query": "phones 30k"},
         "result": "1. https://www.gsmarena.com/a 2. https://croma.com/b", "ms": 900},
        {"kind": "tool", "name": "web_search", "args": {"query": ""}, "result": "nord 5 price india", "ms": 800},
        {"kind": "tool", "name": "web_fetch", "args": {"url": "https://www.gsmarena.com/a"}, "result": "...", "ms": 300},
        {"kind": "done", "duration_ms": 22870, "stats": {"brain": "OpenCode", "model": "opencode/big-pickle"}},
    ]},
]}
with open(os.path.join(folder, "c_test.json"), "w", encoding="utf-8") as f:
    json.dump(chat, f)

out = R.answer({"what": "open", "chat": "c_test", "last": 10})
reply = [m for m in out.get("messages") or [] if m["role"] == "assistant"]
check(f"the chat opens from the PC's file ({len(out.get('messages') or [])} messages)", len(reply) == 1)
m = reply[0] if reply else {}
check(f"a reply carries its model and time, as the window shows them ({m.get('stats')})",
      (m.get("stats") or {}).get("model") == "opencode/big-pickle" and (m.get("stats") or {}).get("duration_ms") == 22870)
check("and its thinking", m.get("thought") == "They want a camera phone.")
steps = m.get("steps") or []
check(f"a search step names the sites it found ({steps[:1]})", steps and steps[0].get("sites") == ["gsmarena.com", "croma.com"])
check(f"a search saved with its query in the result reads that query ({steps[1].get('w') if len(steps) > 1 else ''})",
      len(steps) > 1 and steps[1]["w"] == "Searched the web for nord 5 price india")
check(f"a page read names its site ({steps[2] if len(steps) > 2 else ''})",
      len(steps) > 2 and steps[2]["site"] == "gsmarena.com" and steps[2]["w"] == "Read gsmarena.com/a")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all relay open checks passed")
