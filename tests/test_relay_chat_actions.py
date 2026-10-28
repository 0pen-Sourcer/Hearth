"""The phone pins, renames and deletes a chat through the window's own
conversation routes, the chat list says which are pinned, and an edited
message takes the chat back to just before it.

    .\\.venv\\Scripts\\python.exe tests\\test_relay_chat_actions.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-chat-acts-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.environ["HEARTH_WORKSPACE"])
with open(os.path.join(os.environ["HEARTH_WORKSPACE"], "settings.json"), "w") as f:
    json.dump({"onboarded": True}, f)

from hearth import chat_sync, relay_bridge as RB, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def chat(cid, title, msgs):
    web._save_convo({"id": cid, "title": title, "created": 1, "updated": 1, "source": "relay",
                     "messages": [{"role": r, "content": t} for r, t in msgs]})


s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
RB.WINDOW_URL = f"http://127.0.0.1:{port}"

chat("c_one", "Weekend plans", [("user", "plan my weekend"), ("assistant", "Sure.")])
chat("c_two", "Old chat", [("user", "hi"), ("assistant", "Hello.")])

r = RB.answer({"what": "chat", "action": "pin", "chat": "c_one", "pinned": True})
check(f"pin from the phone pins it on the PC ({r})", r.get("ok") and (web._load_convo("c_one") or {}).get("pinned"))
listed = {c["id"]: c for c in RB.answer({"what": "chats"})["chats"]}
check("the chat list says which chats are pinned", listed["c_one"].get("pinned") is True
      and listed["c_two"].get("pinned") is False)
r = RB.answer({"what": "chat", "action": "pin", "chat": "c_one", "pinned": False})
check("and unpin unpins it", r.get("ok") and not (web._load_convo("c_one") or {}).get("pinned"))

r = RB.answer({"what": "chat", "action": "rename", "chat": "c_one", "title": "  Trip   to Goa "})
saved = web._load_convo("c_one") or {}
check(f"rename from the phone renames it on the PC ({saved.get('title')!r})",
      r.get("ok") and saved.get("title") == "Trip to Goa" and saved.get("titled") is True)
check("and keeps its messages", len(saved.get("messages") or []) == 2)
r = RB.answer({"what": "chat", "action": "rename", "chat": "c_one", "title": "   "})
check("an empty name is refused", r.get("ok") is False and web._load_convo("c_one")["title"] == "Trip to Goa")
r = RB.answer({"what": "chat", "action": "rename", "chat": "c_nope", "title": "x"})
check(f"renaming a chat that isn't there says so ({r.get('error')})", r.get("ok") is False)

r = RB.answer({"what": "chat", "action": "delete", "chat": "c_two"})
check("delete from the phone deletes it on the PC", r.get("ok") and web._load_convo("c_two") is None)
r = RB.answer({"what": "chat", "action": "delete", "chat": "c_two"})
check("deleting it again says it wasn't there", r.get("ok") is False)

# Edit: the chat goes back to just before the edited message.
chat("c_ed", "Edits", [("user", "first"), ("assistant", "A1"), ("user", "second"), ("assistant", "A2"),
                       ("user", "third"), ("assistant", "A3")])
check("an edited message drops it and everything after", RB.drop_from("c_ed", "second", back=1)
      and [m["content"] for m in chat_sync.load("c_ed")["messages"]] == ["first", "A1"])
chat("c_ed2", "Edits", [("user", "same"), ("assistant", "A1"), ("user", "same"), ("assistant", "A2")])
check("the count from the end picks which of two same messages",
      RB.drop_from("c_ed2", "same", back=1) and len(chat_sync.load("c_ed2")["messages"]) == 0)
chat("c_ed3", "Edits", [("user", "keep me"), ("assistant", "A1")])
check("words that don't match drop nothing", not RB.drop_from("c_ed3", "something else", back=0)
      and len(chat_sync.load("c_ed3")["messages"]) == 2)

# A notification for a question with a long command still fits one push.
import asyncio  # noqa: E402
noticed = []


class _Room:
    async def notice(self, obj, urgent=False):
        noticed.append(obj)


loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
relay = RB.Relay(loop)
relay.rooms["p1"] = _Room()
relay.notify("p1", {"kind": "ask", "title": "JARVIS asks", "body": "Before I go on: may I run `" + "x" * 5000 + "`?", "ask": "a1"})
sealed = json.dumps(noticed[-1]) if noticed else ""
check(f"a long question's notification is cut to fit a push ({len(sealed)} bytes)",
      noticed and len(noticed[-1]["body"]) <= 303 and noticed[-1]["ask"] == "a1")

srv.shutdown()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat action checks passed")
