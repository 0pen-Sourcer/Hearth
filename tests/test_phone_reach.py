"""The agent knows it can reach the person's phone only when it can: with Plus on
and a phone paired, the system prompt (every brain's) says send_to_phone puts a
message there and call_phone rings it, and when a ring is right. "Call me when
the backup finishes" rings once the backup is done, even with the agent's own
calls off; a routine reply or a message never rings, and a call on the agent's
own judgment needs calls on. Stand-in model and phone; the real prompt, tools,
call rules and bridge.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_reach.py
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
os.environ["HEARTH_RELAY_URL"] = "http://127.0.0.1:9"
os.environ["HEARTH_WINDOW_URL"] = "http://127.0.0.1:9"     # the window's event stream (publish is stood in)

from hearth import call_me as CM, claude_brain, headless, live_bus, memory_extract, persona, plus  # noqa: E402
from hearth import relay_bridge as RB, relay_crypto as R, reminders, tools  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.note_turn_async = lambda *a, **k: None
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"
tools.WORKSPACE = WS
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def settings(**kv):
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(dict({"approval_mode": "auto", "auto_reviewer": "off"}, **kv), f)


def pair(on: bool):
    RB._write({"phones": [{"pub": R.b64(b"p" * 32), "name": "Pixel", "at": 1}]} if on else {})


asked_dodo: list = []
plus._post = lambda path, body, timeout=15: asked_dodo.append(path) or {"status": 0, "json": {}}


def plus_on(on: bool):
    if on:
        plus._write({"key": "k-test", "instance": "i1", "valid": True, "checked": time.time() - 3 * 24 * 3600})
    else:
        try:
            os.remove(plus._file())
        except OSError:
            pass


def line_in(prompt: str) -> bool:
    return "call_phone" in prompt and "send_to_phone" in prompt and "paired" in prompt


settings()

# ---------------------------------------------------------------- the line, only when true
pair(False); plus_on(False)
check("no Plus and no phone: the prompt says nothing about a phone", not line_in(persona.system_prompt()))
pair(True)
check("a phone paired but no Plus: nothing", not line_in(persona.system_prompt()))
pair(False); plus_on(True)
check("Plus but no phone paired: nothing", not line_in(persona.system_prompt()))
pair(True)
p1 = persona.system_prompt()
check("Plus on and a phone paired: the prompt says it can send there and call", line_in(p1))
check("and when a ring is right: asked for, or important while they're away, never a routine reply",
      "asked to be called" in p1 and "away from" in p1 and "never for a routine reply" in p1)
check("reading Plus for the prompt never asks the licence server (a day-old check included)", not asked_dodo)
check("the prompt is byte-identical turn to turn, so the cached prefix holds", persona.system_prompt() == p1)
check("an agent brain (Claude Code, Codex...) is told the same",
      line_in(claude_brain.persona_text(False)))

# ---------------------------------------------------------------- the tools say which is which
defs = {t["name"]: t for t in tools.TOOL_DEFINITIONS}
check("the main agent has a tool that rings the phone", "call_phone" in defs and "call_phone" in tools._HANDLERS)
check("call_phone says it rings, when to, and that routine replies go by send_to_phone",
      "Ring" in defs.get("call_phone", {}).get("description", "")
      and "send_to_phone" in defs.get("call_phone", {}).get("description", "")
      and "routine" in defs.get("call_phone", {}).get("description", ""))
check("send_to_phone says it never rings and points to call_phone",
      "never rings" in defs["send_to_phone"]["description"] and "call_phone" in defs["send_to_phone"]["description"])
check("a benchmark dry run never rings a real phone", "call_phone" in tools._DRYRUN_GUARDED)

# ---------------------------------------------------------------- "call me when the backup finishes"
published: list = []
seq: list = []
live_bus.publish = lambda ev: (published.append(ev), seq.append(("bus", ev.get("kind"))))
reminders.idle_seconds = lambda: 9999.0                 # away from the PC
RB._seen("*", False)                                    # the phone bridge is up (this process)
settings(call_me=False, call_me_quiet=False)            # the agent's own calls off; asked-for ones still ring


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


def model(*steps, answer="Done."):
    replies = [[chunk(tool=NS(index=0, id=f"call_{i}", type="function",
                              function=NS(name=n, arguments=json.dumps(a))), finish="tool_calls")]
               for i, (n, a) in enumerate(steps)]
    replies.append([chunk(content=answer), chunk(finish="stop")])

    async def fake(client, **kw):
        return Stream(replies.pop(0))
    return fake


def turn(prompt, *steps, answer="Done."):
    headless.chat_create = model(*steps, answer=answer)
    out: list = []

    def emit(kind, **f):
        if kind == "tool_result":
            seq.append(("result", f.get("name")))
            out.append((f.get("name"), str(f.get("content") or "")))
    asyncio.run(headless.run_once(prompt, model="m", history=[], chat_id="c_backup",
                                  permission_check=lambda n, a: "allow", emit=emit))
    return out


src = os.path.join(WS, "notes")
os.makedirs(src, exist_ok=True)
open(os.path.join(src, "a.txt"), "w").write("x")
dst = os.path.join(WS, "notes_backup")
results = turn("back up my notes folder and call me when the backup finishes",
               ("run_command", {"command": f"Copy-Item -Recurse '{src}' '{dst}'"}),
               ("call_phone", {"message": "The backup of your notes finished with no errors.", "asked": True}),
               answer="Backed up, and I'm calling your phone.")
rings = [e for e in published if e.get("kind") == "call_me"]
check(f"the stand-in's call_phone asked for a ring ({results[-1][1] if results else None!r})",
      len(rings) == 1 and results and results[-1][1].startswith("Calling"))
order = [x for x in seq if x in (("result", "run_command"), ("bus", "call_me"))]
check(f"after the backup ran, not before ({order})", order == [("result", "run_command"), ("bus", "call_me")]
      and os.path.isdir(dst))
ev = rings[0] if rings else {}
check(f"saying what finished, in this chat ({ev.get('say')!r})",
      "backup of your notes finished" in ev.get("say", "") and ev.get("c") == "c_backup" and ev.get("asked") is True)

bridge = RB.Bridge()
pushed, notes = [], []
bridge.adapter._push = lambda chat, obj: pushed.append((chat, dict(obj))) or "m"
bridge.adapter.notify = lambda chat, obj, urgent=False: notes.append((chat, dict(obj), urgent))
bridge.adapter.send_file = lambda chat, path: pushed.append((chat, {"type": "file", "path": path}))
bridge.adapter.rooms["phone1"] = types.SimpleNamespace(phone=b"p" * 32, chat="phone1", peer_up=False, chan=None, call=None)
bridge.on_window_event(ev)
end = time.time() + 3
while not notes and time.time() < end:
    time.sleep(0.05)
check("the phone bridge rings for it, with the agent's own calls off",
      notes and notes[0][1]["kind"] == "ring" and "backup" in notes[0][1]["body"])
bridge.decline(bridge.rings.now["id"]) if bridge.rings.now else None

settings(call_me=False, call_me_quiet=True, call_me_quiet_from="00:00", call_me_quiet_to="23:59")
published.clear()
said = CM.ask_for("The backup finished.", asked=True)
check(f"in quiet hours it doesn't ring, and says so ({said!r})", said.startswith("Not calling") and not published)
settings(call_me=False, call_me_quiet=False)
reminders.idle_seconds = lambda: 5.0
said = CM.ask_for("The backup finished.", asked=True)
check(f"nor while they're at the PC ({said!r})", said.startswith("Not calling") and not published)
reminders.idle_seconds = lambda: 9999.0
pair(False)
said = CM.ask_for("The backup finished.", asked=True)
check(f"with no phone paired it says it can't, not that it's calling ({said!r})",
      said.startswith("Couldn't call") and not published)
pair(True)

# ---------------------------------------------------------------- nothing rings for routine replies
published.clear()
turn("what's on my desktop?", answer="Three folders and a shortcut to Steam.")
check("a routine reply rings nothing", not [e for e in published if e.get("kind") == "call_me"])
results = turn("text me the wifi password", ("send_to_phone", {"message": "Wifi: hunter2"}),
               answer="Sent it to your phone.")
sent = [e for e in published if e.get("kind") == "to_phone"]
check(f"a message to the phone goes as a message to the Inglen app ({results[-1][1] if results else None!r})",
      len(sent) == 1 and sent[0]["text"] == "Wifi: hunter2" and results[-1][1].startswith("Sent"))
check("and rings nothing", not [e for e in published if e.get("kind") == "call_me"])
pushed.clear(); notes.clear()
bridge.on_window_event(sent[0] if sent else {})
end = time.time() + 3
while not notes and time.time() < end:
    time.sleep(0.05)
check("the bridge puts it on the phone with a notification, not a ring",
      any(o.get("type") == "text" and o.get("text") == "Wifi: hunter2" for _, o in pushed)
      and notes and notes[0][1]["kind"] == "done" and not any(o.get("type") == "call.ring" for _, o in pushed))

settings(call_me=False, call_me_quiet=False)
published.clear()
results = turn("ok thanks", ("call_phone", {"message": "All good here.", "asked": False}), answer="Anytime.")
check(f"a call on the agent's own judgment, with its calls off, doesn't ring ({results[-1][1] if results else None!r})",
      not published and results and results[-1][1].startswith("Not calling"))
from hearth import memory_mcp  # noqa: E402
check("same for a coding agent's call_me", memory_mcp.call_me("Status: still compiling.", asked=False).startswith("Not calling")
      and not published)
settings(call_me=True, call_me_quiet=False)
said = CM.ask_for("The server is down and the demo is in ten minutes.", asked=False)
check(f"with calls on, one it judged important rings ({said!r})",
      said.startswith("Calling") and published and published[-1].get("judged") is True)
settings(call_me=False, call_me_quiet=False)
check("and the bridge drops it if calls were turned off meanwhile", not CM.allowed(published[-1] if published else {}))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all phone reach checks passed")
