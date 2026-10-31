"""Asked for something only the phone could do, without Plus or a paired phone, the
agent is told to say once, plainly, that Plus would reach their phone and where it
is turned on: once per chat, only at that moment, never in the system prompt, not
when the phone was reached another way, not for a call on its own judgment. And the
agent's name comes from settings everywhere a person or the model reads it: a
rename reaches a bridge's next turn and what the phone shows. Stand-in model and
phone; the real tools, rules and prompt.

    .\\.venv\\Scripts\\python.exe tests\\test_plus_hint.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import types
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401

WS = _home.WORKSPACE
os.environ.update(HEARTH_WORKSPACE=WS, HEARTH_NO_ACTIVITY_LOG="1")
os.environ["HEARTH_RELAY_URL"] = os.environ["HEARTH_WINDOW_URL"] = "http://127.0.0.1:9"

from hearth import call_me as CM, channel_core as cc, headless, live_bus, memory_extract, persona, plus  # noqa: E402
from hearth import relay_bridge as RB, relay_crypto as R, reminders, tools  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.note_turn_async = lambda *a, **k: None
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"
tools.WORKSPACE = WS
plus._post = lambda *a, **k: {"status": 0, "json": {}}
published: list = []
live_bus.publish = lambda ev: published.append(ev)
reminders.idle_seconds = lambda: 9999.0
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def settings(**kv):
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(dict({"approval_mode": "auto", "auto_reviewer": "off", "call_me_quiet": False}, **kv), f)


def chunk(content=None, tool=None, finish=None):
    return NS(choices=[NS(index=0, delta=NS(content=content, reasoning_content=None, model_extra={},
                                             tool_calls=[tool] if tool else None), finish_reason=finish)],
              usage=None, model_extra={})


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()


def turn(chat, tool, args, answer="Okay."):
    replies = [[chunk(tool=NS(index=0, id="call_0", type="function",
                              function=NS(name=tool, arguments=json.dumps(args))), finish="tool_calls")],
               [chunk(content=answer), chunk(finish="stop")]]
    sent: list = []

    async def fake(client, **kw):
        sent.append(kw.get("messages"))
        return Stream(replies.pop(0))
    headless.chat_create = fake
    out: list = []
    asyncio.run(headless.run_once("call me when the backup finishes", model="m", history=[], chat_id=chat,
                                  permission_check=lambda n, a: "allow",
                                  emit=lambda k, **f: out.append(str(f.get("content") or "")) if k == "tool_result" else None))
    return (out[-1] if out else ""), sent


HINT = "Settings > Plus"
ONCE = "Tell them once"
settings()

# ---------------------------------------------------------------- no Plus, nothing paired
got, sent = turn("c1", "call_phone", {"message": "The backup finished.", "asked": True})
check(f"asked to call without Plus: the result says it can't, and to tell them once about Plus ({got[:90]!r}...)",
      got.startswith("Couldn't call") and ONCE in got and HINT in got and not published)
check("the next model call sees it, so the reply can say it",
      sent and any(ONCE in str(m.get("content")) for m in sent[-1] if m.get("role") == "tool"))
check("nothing about Plus in the system prompt", "Inglen Plus" not in persona.system_prompt()
      and "call_phone" not in persona.system_prompt())
check("reading Plus doesn't make this process think it is the window (its events would stay here)",
      live_bus.IN_WINDOW is False)
got, _ = turn("c1", "call_phone", {"message": "The backup finished.", "asked": True})
check(f"asked again in the same chat: not again ({got!r})", got.startswith("Couldn't call") and ONCE not in got)
got, _ = turn("c2", "call_phone", {"message": "The backup finished.", "asked": True})
check("in another chat, once there too", ONCE in got)
got, _ = turn("c3", "call_phone", {"message": "Something came up.", "asked": False})
check(f"a call on its own judgment never brings it up ({got!r})", ONCE not in got and not published)
got, _ = turn("c3", "send_to_phone", {"message": "Wifi: hunter2"})
check(f"send_to_phone with no Plus and no chat app: said once ({got[:80]!r}...)",
      got.startswith("Error: no phone to send to") and ONCE in got)
got, _ = turn("c3", "send_to_phone", {"message": "Wifi: hunter2"})
check("and not again in that chat", got.startswith("Error") and ONCE not in got)

cc.config_of = lambda ch: {"bot_token": "t"} if ch == "telegram" else {}
cc.owner_of = lambda ch: "42" if ch == "telegram" else ""
cc.push = lambda ch, text: "Sent to your Telegram."
got, _ = turn("c4", "send_to_phone", {"message": "Wifi: hunter2"})
check(f"reached through Telegram instead: nothing about Plus, and it isn't used up ({got!r})",
      got == "Sent to your Telegram." and ONCE in CM.plus_hint("c4"))

# ---------------------------------------------------------------- Plus on, paired, link down
RB._write({"phones": [{"pub": R.b64(b"p" * 32), "name": "Pixel", "at": 1}]})
plus._write({"key": "k", "valid": True, "checked": time.time()})
try:
    os.remove(RB.LIVE_FILE)
except OSError:
    pass
got, _ = turn("c5", "call_phone", {"message": "The backup finished.", "asked": True})
check(f"Plus on and paired but the link down: says why, nothing about buying Plus ({got!r})",
      got.startswith("Couldn't call") and "isn't running" in got and ONCE not in got)
os.remove(plus._file())
got, _ = turn("c6", "call_phone", {"message": "The backup finished.", "asked": True})
check(f"a phone still paired but Plus off: it can't, and says Plus once ({got[:60]!r}...)",
      got.startswith("Couldn't call: Inglen Plus isn't on") and ONCE in got)

# ---------------------------------------------------------------- the name, from settings
settings(agent_name="FRIDAY")
persona.NAME = "Inglen"                                  # what a bridge started before the rename holds
cc.follow_saved_brain()
check("a rename in the window reaches a bridge's next turn", "You are FRIDAY." in persona.system_prompt())
persona.NAME = "Inglen"
bridge = RB.Bridge()
notes: list = []
bridge.adapter._push = lambda chat, obj: "m"
bridge.adapter.send_file = lambda chat, path: None
bridge.adapter.notify = lambda chat, obj, urgent=False: notes.append(dict(obj))
bridge.adapter.rooms["phone1"] = types.SimpleNamespace(phone=b"p" * 32, chat="phone1", peer_up=False, chan=None, call=None)
bridge.to_phone({"kind": "to_phone", "text": "hi"})
check(f"a message sent to the phone is signed with the name in settings ({notes[-1]['title'] if notes else None!r})",
      notes and notes[-1]["title"] == "FRIDAY")
html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("the window has no hardcoded agent name in its name slots", "data-agent-name>JARVIS<" not in html)

# ---------------------------------------------------------------- whose events stay local
from hearth import web  # noqa: E402
check("importing the window's module (bridges and the terminal do) doesn't make a process the window",
      live_bus.IN_WINDOW is False)
srv = web._QuietHTTPServer(("127.0.0.1", 0), web.HearthHandler)
check("serving it does", live_bus.IN_WINDOW is True)
srv.server_close()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all Plus hint and naming checks passed")
