"""The Stop button on a chat app's turn (the phone's): the turn ends through the same cancel the
window's Stop uses, promptly, with no step run after it, and the chat is free again.

Real headless.run_once with stand-in model calls: a reply that streams and never finishes, and a
model that asks for one step after another.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_stop.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401  (never the real ~/.hearth)
WS = tempfile.mkdtemp(prefix="hearth-phone-stop-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["LOCAL_MODEL"] = "stand-in"

from hearth import channel_core as cc, headless, memory_extract  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
cc.follow_saved_brain = lambda *a, **k: {}
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def chunk(**delta):
    d = dict(content=None, reasoning_content=None, tool_calls=None, model_extra={})
    d.update(delta)
    return NS(choices=[NS(index=0, delta=NS(**d), finish_reason=None)], usage=None, model_extra={})


class Stream:
    """Chunks, then (hang) nothing more until closed."""

    def __init__(self, pieces, hang=False):
        self.pieces, self.hang, self.closed = pieces, hang, False

    def __aiter__(self):
        async def gen():
            for p in self.pieces:
                yield p
            while self.hang and not self.closed:
                await asyncio.sleep(0.05)
        return gen()

    async def close(self):
        self.closed = True


class Phone(cc.Adapter):
    name, label = "relay", "Phone"

    def __init__(self):
        self.sent = []

    def send(self, chat, text, reply_to=None):
        self.sent.append(text)
        return None


ran: list = []


def fake_tool(name, args):
    ran.append(args.get("path"))
    time.sleep(0.3)
    return "contents"


headless.execute_tool = fake_tool
mode = {"kind": "writing"}


calls: list = []


async def fake_create(client, **kw):
    calls.append(time.time())
    if mode["kind"] == "writing":
        return Stream([chunk(content="Here is a long answer that keeps going")], hang=True)
    if "told you to stop" in str((kw.get("messages") or [{}])[-1].get("content") or ""):
        return Stream([chunk(content="Stopped. I had read step one; nothing else ran.")])   # asked after a stop: words only
    n = len(ran) + 1
    call = NS(index=0, id=f"c{n}", type="function", function=NS(name="read_file", arguments='{"path": "step%d.txt"}' % n))
    return Stream([chunk(tool_calls=[call])])


headless.chat_create = fake_create


def turn(stop_when):
    phone = Phone()
    hub = cc.Hub(phone, configured_senders=["p1"], persona="JARVIS")
    hub.receive("p1", "p1", "go on", cid="c_stop")
    end = time.time() + 30                       # the first turn imports the model client, which takes a while
    while not stop_when() and time.time() < end:
        time.sleep(0.05)
    pressed = hub.stop("p1", "c_stop")
    t0 = time.time()
    while "c_stop" in hub._busy and time.time() - t0 < 15:
        time.sleep(0.05)
    return hub, phone, pressed, time.time() - t0


# Stop while the reply is being written (a model that would never finish).
mode["kind"] = "writing"
hub, phone, pressed, took = turn(lambda: bool(calls))
check(f"Stop on a reply being written ends the turn promptly ({took:.1f} s)", pressed and took < 3)
check("and the chat is free again", "c_stop" not in hub._busy)
check(f"and the phone hears the turn end with what it had written, not as a failure ({phone.sent[-1:] if phone.sent else None})",
      bool(phone.sent) and "couldn't get a response" not in phone.sent[-1].lower())

# Stop while steps run: no step runs after it.
ran.clear()
mode["kind"] = "steps"
hub, phone, pressed, took = turn(lambda: len(ran) >= 1)
n = len(ran)
time.sleep(1.5)
check(f"Stop between steps ends the turn promptly ({took:.1f} s)", pressed and took < 3)
check(f"and no step runs after it ({ran})", len(ran) == n and n <= 2)
check("Stop on a chat with nothing running says so", cc.Hub(Phone(), configured_senders=["p1"]).stop("p1", "c_none") is False)

# Letting it go, typed or said: a stop, read narrowly.
from hearth.stop_words import is_stop  # noqa: E402
lets_go = ["Leave it", "leave it.", "Leave it, I'll do it", "leave it for now", "forget it", "forget about it",
           "drop it", "actually never mind", "nvm", "skip it", "don't bother", "stop that", "never mind"]
requests = ["Leave the file as is and summarise", "leave it in the downloads folder", "drop it into the shared folder",
            "forget the old plan and use this one", "no need to ask, just do it", "cancel my 3pm meeting"]
check(f"'leave it', 'forget it', 'drop it' and the like are a stop ({[s for s in lets_go if not is_stop(s)]} missed)",
      all(is_stop(s) for s in lets_go))
check(f"and a request that only starts like one isn't ({[s for s in requests if is_stop(s)]})",
      not any(is_stop(s) for s in requests))

# "Leave it" sent while steps run, the way the phone sends it (a message into the busy chat): the steps stop.
ran.clear()
mode["kind"] = "steps"
phone = Phone()
hub = cc.Hub(phone, configured_senders=["p1"], persona="JARVIS")
hub.receive("p1", "p1", "go on", cid="c_said")
end = time.time() + 30
while not ran and time.time() < end:
    time.sleep(0.05)
said = hub.receive("p1", "p1", "Leave it", cid="c_said")
t0 = time.time()
while "c_said" in hub._busy and time.time() - t0 < 15:
    time.sleep(0.05)
n = len(ran)
time.sleep(1.5)
check(f"'Leave it' sent mid-turn from the phone ends the turn ({said}, {time.time() - t0 - 1.5:.1f} s)",
      said == "joined" and "c_said" not in hub._busy and time.time() - t0 - 1.5 < 5)
check(f"and no step runs after it ({ran})", len(ran) == n and n <= 2)

# Where it is kept: after the steps taken before it, on the phone and in the window.
from hearth import chat_sync, relay_bridge as RB  # noqa: E402
saved = (chat_sync.load("c_said") or {}).get("messages") or []
mine = [m for m in saved if m.get("role") == "user" and m.get("content") == "Leave it"]
reply = next((m for m in saved if m.get("role") == "assistant"), {})
kinds = [e.get("kind") for e in reply.get("events") or []]
check(f"it is kept in the reply as a steer, after the step before it ({kinds})",
      "steer" in kinds and "tool" in kinds and kinds.index("tool") < kinds.index("steer"))
check("and as their own message for the model, marked as said mid-turn", mine and mine[0].get("steered") is True)
got = RB.answer({"what": "open", "chat": "c_said"}, "relay:p1")
order = [(m["role"], m.get("text") or ("steps" if m.get("steps") else "")) for m in got.get("messages") or []]
check(f"the phone shows it after the steps that came before it, not above them ({order})",
      order[:3] == [("user", "go on"), ("assistant", "steps"), ("user", "Leave it")]
      and order[-1][0] == "assistant" and "Stopped" in order[-1][1])
html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("the window draws it in the reply (its steer) and skips the copy kept for the model",
      "m.role === 'user' && m.steered) return null" in html and "ev.kind === 'steer'" in html)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all phone stop checks passed")
