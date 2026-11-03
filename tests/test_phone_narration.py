"""A turn from the phone keeps the words written between its steps: saved as the window saves its
own turns, so the chat reopened on the phone or in the window reads text, step, text, step, answer,
not every step in one run above the last message.

Real headless.run_once with stand-in model calls; an agent brain that ends with the whole turn's
words isn't doubled.

    .\\.venv\\Scripts\\python.exe tests\\test_phone_narration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401  (never the real ~/.hearth)
WS = tempfile.mkdtemp(prefix="hearth-phone-narration-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["LOCAL_MODEL"] = "stand-in"

from hearth import channel_core as cc, chat_sync, headless, memory_extract, relay_bridge as RB  # noqa: E402

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
    def __init__(self, pieces):
        self.pieces = pieces

    def __aiter__(self):
        async def gen():
            for p in self.pieces:
                yield p
        return gen()

    async def close(self):
        pass


class Phone(cc.Adapter):
    name, label = "relay", "Phone"

    def __init__(self):
        self.sent, self.steps = [], []

    def send(self, chat, text, reply_to=None):
        self.sent.append(text)
        return None

    def step(self, chat, words, tool=None, **kw):
        self.steps.append(words)


headless.execute_tool = lambda name, args: "contents of " + str(args.get("path"))
SAID = ["Let me read the first file. ", "Now the second one. ", "Both are read: they match."]


CALLS = [0]


async def fake_create(client, **kw):
    n = CALLS[0]
    CALLS[0] += 1
    if n >= 2:
        return Stream([chunk(content=SAID[2])])
    call = NS(index=0, id=f"c{n}", type="function",
              function=NS(name="read_file", arguments='{"path": "f%d.txt"}' % (n + 1)))
    return Stream([chunk(content=SAID[n]), chunk(tool_calls=[call])])


headless.chat_create = fake_create


def turn(cid):
    phone = Phone()
    hub = cc.Hub(phone, configured_senders=["p1"], persona="JARVIS")
    hub.receive("p1", "p1", "read both files", cid=cid)
    end = time.time() + 40
    while (not phone.sent or cid in hub._busy) and time.time() < end:
        time.sleep(0.05)
    return phone


phone = turn("c_words")
reply = next((m for m in (chat_sync.load("c_words") or {}).get("messages") or [] if m.get("role") == "assistant"), {})
text = reply.get("content") or ""
check(f"the saved reply keeps the words written before each step ({text!r})",
      all(s.strip() in text for s in SAID))
check(f"and the phone is still sent only the answer ({phone.sent})", phone.sent and phone.sent[-1].strip() == SAID[2])
pos = [e.get("pos") for e in reply.get("events") or [] if e.get("kind") == "tool"]
check(f"each step's place is inside the saved words ({pos}, {len(text)} letters)",
      len(pos) == 2 and 0 < pos[0] < pos[1] < len(text))
got = RB.answer({"what": "open", "chat": "c_words"}, "relay:p1")
msg = next((m for m in got.get("messages") or [] if m.get("role") == "assistant"), {})
shape = ["steps" if "steps" in p else "text" for p in msg.get("parts") or []]
check(f"the phone reopens it as text, step, text, step, answer ({shape})",
      shape == ["text", "steps", "text", "steps", "text"])

# An agent brain ends with the whole turn's words: kept once, not twice.
_real = headless.run_once


async def agent_turn(text, emit=None, **kw):
    emit("assistant_chunk", content=SAID[0])
    emit("tool_call", name="read_file", args={"path": "f1.txt"})
    emit("tool_result", name="read_file", content="x")
    emit("assistant_chunk", content=SAID[2])
    emit("assistant", content=SAID[0] + SAID[2])


headless.run_once = agent_turn
turn("c_agent")
reply = next((m for m in (chat_sync.load("c_agent") or {}).get("messages") or [] if m.get("role") == "assistant"), {})
text = reply.get("content") or ""
check(f"an agent brain's closing words aren't doubled ({text!r})", text.count(SAID[0].strip()) == 1 and SAID[2] in text)
headless.run_once = _real

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all phone narration checks passed")
