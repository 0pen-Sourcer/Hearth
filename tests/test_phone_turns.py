"""A turn from the phone app or a chat app: what its tools send is filed under it,
"send it to my phone" means this same conversation, and the question before a
step says it in words.

What the owner saw: two "Thinking" rows that never ended (sends from a tool's
or an approval's thread had no turn, so the phone started a second turn), a
question saying "may I use send_to_phone (message=... If you)?", and the brain
saying it couldn't send a photo to the phone (send_to_phone took only text and
went to Telegram), pushed by a screenshot result that shouted NEXT STEP orders.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_turns.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-phone-turns-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
WS = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(WS)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "approval_mode": "ask"}, f)
sys.path.insert(0, ROOT)

from hearth import channel_core as cc, headless, relay_bridge as RB, tools  # noqa: E402

cc.ACCESS_FILE = os.path.join(WS, "channel_access.json")
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Phone(cc.Adapter):
    name, label, max_len = "phone", "the phone app", 4000

    def __init__(self):
        self.out = []

    def _rec(self, kind, chat, **kw):
        self.out.append(dict(kw, kind=kind, chat=chat, **cc.turn_context()))

    def send(self, chat, text, reply_to=None):
        self._rec("text", chat, text=text)
        return len(self.out)

    def send_file(self, chat, path):
        self._rec("file", chat, path=path)

    def ask(self, chat, text):
        self._rec("ask", chat, text=text)

    def step(self, chat, words, tool="", site=""):
        self._rec("step", chat, words=words)

    def edit(self, chat, msg_id, text):
        return True


SHOT = os.path.join(WS, "shot.png")
open(SHOT, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
seen = {}


async def model(prompt, *, emit, permission_check=None, **k):
    emit("assistant_chunk", content="Taking one now. ")
    emit("tool_call", name="take_screenshot", args={})
    time.sleep(0.05)
    emit("tool_result", name="take_screenshot", content=f"Saved the screenshot: {SHOT} (2560x1440).")
    # A tool runs on a thread of its own, as headless runs them.
    seen["tool"] = await asyncio.to_thread(lambda: dict(cc.turn_context()))
    seen["allow"] = await asyncio.to_thread(permission_check, "send_to_phone", {"path": SHOT})
    seen["sent"] = await asyncio.to_thread(tools._send_to_phone, {"message": "Here it is.", "path": SHOT})
    seen["q"] = await asyncio.to_thread(permission_check, "move_path", {"source": SHOT, "destination": "D:/x"})
    emit("assistant", content="Done.")
    return 0

headless.run_once = model
app = Phone()
hub = cc.Hub(app, configured_senders=["me"], persona="JARVIS")
cc.ASK_TIMEOUT_S = 1
hub.receive("me", "me", "send me a screenshot")
end = time.time() + 10
while hub._busy and time.time() < end:
    time.sleep(0.05)
for t in hub.threads:
    t.join(3)

turns = {o.get("t") for o in app.out}
check(f"everything the turn sent, from its tools' threads too, carries its turn ({turns})",
      len(turns) == 1 and None not in turns and "" not in turns)
check("a tool's thread knows the turn", bool(seen.get("tool", {}).get("t")))
check(f"sending to the phone in a phone turn doesn't ask ({seen.get('allow')})", seen.get("allow") == "allow")
files = [o for o in app.out if o["kind"] == "file"]
check(f"and sends the file into this same conversation ({seen.get('sent')!r})",
      len(files) == 1 and files[0]["path"] == SHOT and "here, in this conversation" in seen.get("sent", ""))
check("with its message", any(o["kind"] == "text" and o["text"] == "Here it is." for o in app.out))
q = [o["text"] for o in app.out if o["kind"] == "ask"]
check(f"the question says the step in words, not the tool's name {q}",
      q and q[0].startswith("Move shot.png to D:/x?") and "move_path" not in q[0])
from hearth import chat_sync  # noqa: E402
saved = (chat_sync.load(cc.chat_id_for(app.name, "me")) or {}).get("messages") or []
ev = [e for e in (saved[-1].get("events") or []) if e.get("kind") == "tool"] if saved else []
check(f"the window gets each step with what came back, how long and where it was in the reply ({ev[:1]})",
      ev and ev[0]["name"] == "take_screenshot" and "Saved the screenshot" in ev[0]["result"]
      and ev[0]["ms"] >= 40 and ev[0]["pos"] == len("Taking one now. ") and not any(k.startswith("_") for k in ev[0]))
asked = [e for e in (saved[-1].get("events") or []) if e.get("kind") == "ask"] if saved else []
check(f"a question the turn asked is kept with its steps, with its answer ({asked})",
      len(asked) == 1 and asked[0]["text"].startswith("Move shot.png") and asked[0]["answer"] is False)
opened = RB.answer({"what": "open", "chat": cc.chat_id_for(app.name, "me")})["messages"]
folds = [m for m in opened if m.get("ask")]
check(f"and a chat opened again on the phone shows it answered, where it was asked ({folds})",
      len(folds) == 1 and folds[0]["answer"] is False and "Move shot.png" in folds[0]["text"]
      and opened.index(folds[0]) == len(opened) - 2)
# A file sent from the phone: their message is what they wrote, with the file as an attachment.
seen.clear()
hub.receive("me", "me", "See", files=[SHOT])
end = time.time() + 10
while hub._busy and time.time() < end:
    time.sleep(0.05)
for t in hub.threads:
    t.join(3)
saved = (chat_sync.load(cc.chat_id_for(app.name, "me")) or {}).get("messages") or []
me = [m for m in saved if m.get("role") == "user"][-1]
check(f"their message shows what they wrote, the file as an attachment ({me.get('displayContent')!r}, {me.get('attachments')})",
      me.get("displayContent") == "See" and me.get("attachments") == [{"name": "shot.png", "path": SHOT, "isImage": True}])
check(f"the model is told about it in plain words ({me['content']!r})",
      me["content"].startswith("See\n[Attached: shot.png, saved at ") and "view_image shows it" in me["content"]
      and "They sent" not in me["content"])
got = RB.answer({"what": "open", "chat": cc.chat_id_for(app.name, "me")})["messages"]
mine = [m for m in got if m.get("role") == "user"][-1]
check(f"the phone shows the words and the file card, not the note ({mine.get('text')!r})",
      mine.get("text") == "See" and [c["name"] for c in mine.get("files") or []] == ["shot.png"])
check("send_to_phone in plain words", cc._what("send_to_phone", {"message": "Relay test"}) == 'send this to your phone: "Relay test"')

check("from the window (no app turn), it still asks", tools.mode_allows("send_to_phone", {"message": "x"}) is False)
check("and with no app connected it says so",
      tools._send_to_phone({"message": "x"}).startswith("Error: no phone to send to")
      and "no chat app" in tools._send_to_phone({"message": "x"}))

src = open(os.path.join(ROOT, "hearth", "tools.py"), encoding="utf-8").read()
check("a screenshot's result doesn't shout orders", "TELL THEM WHAT'S" not in src and "RIGHT NOW in" not in src)
hsrc = open(os.path.join(ROOT, "hearth", "headless.py"), encoding="utf-8").read()
check("and a next-step note is never written as if the person said it", "Don't narrate, don't describe, don't ask. Act." not in hsrc)

# Said while a turn runs (a call, mostly): kept as their own words, and one said too late to
# be read is the next turn, not lost. Owner, 2026-10-29: "his words don't appear" after a call.
said_in, gate = [], {"go": False}


async def listening(prompt, *, emit, drain_steer=None, **k):
    said_in.append(prompt)
    end = time.time() + 5
    while not gate["go"] and time.time() < end:
        await asyncio.sleep(0.02)
    heard = drain_steer() if drain_steer else []
    emit("assistant", content="Heard: " + ", ".join(heard) if heard else "Okay.")
    if prompt == "start the call":
        gate["late"] = hub.receive("talk", "me", "and one more thing")
    return 0

headless.run_once = listening
hub.receive("talk", "me", "start the call")
time.sleep(0.2)
check("a word said while it runs joins the turn", hub.receive("talk", "me", "also check the logs") == "joined")
gate["go"] = True
end = time.time() + 10
while (hub._busy or len(said_in) < 2) and time.time() < end:
    time.sleep(0.05)
for t in list(hub.threads):
    t.join(3)
saved = (chat_sync.load(cc.chat_id_for(app.name, "talk")) or {}).get("messages") or []
words = [m["content"] for m in saved if m.get("role") == "user"]
check(f"what they said during the turn is kept as their own message ({words})",
      words[:2] == ["start the call", "also check the logs"])
check(f"one said too late for the model is the next turn, not lost ({gate.get('late')}, {said_in})",
      gate.get("late") == "joined" and said_in[-1] == "and one more thing" and "and one more thing" in words)

b = RB.Bridge.__new__(RB.Bridge)
b.hub, b.agent_runs = hub, {"as_codex_x": {"t": "agent1"}}
hub._busy["k"] = {"t": "turn9"}
check(f"a phone that reconnects is told which turns are running ({b.running()})", sorted(b.running()) == ["agent1", "turn9"])

print("\n" + ("all phone turn checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
