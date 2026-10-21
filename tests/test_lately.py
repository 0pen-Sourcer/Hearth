"""What has been going on lately reaches the next message.

"I got something, take a guess" was answered like a stranger: the model saw only
that line. The memory pass now notes where each chat stands and what is still
open, and a few lines of it go in front of the next message (after the
history, so the cached prefix holds). An agent brain's turn gets the same, and
its turn runs the memory pass too. Stand-in model calls.

    .\\.venv\\Scripts\\python.exe tests\\test_lately.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-lately-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import headless, memory_extract, threads  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


now = time.time()
DAY = 86400

# ---- the memory pass notes where a chat stands
reply = {"facts": [{"title": "wants-edge-60-pro", "category": "user", "description": "Wants the Motorola Edge 60 Pro.",
                    "body": "For the telephoto camera.", "confidence": 4, "volatility": "transient"}],
         "thread": {"topic": "Phone hunt", "where": "Looking for the Motorola Edge 60 Pro, mainly for the telephoto.",
                    "open": ["Hasn't bought it yet"], "closed": []}}
asked = []
saved, warns = memory_extract.extract_and_save(
    [{"role": "user", "content": "I really want the Edge 60 Pro, still hunting for it"},
     {"role": "assistant", "content": "Good pick for the telephoto."}],
    lambda system, user: asked.append(user) or "```json\n" + json.dumps(reply) + "\n```",
    chat_id="c_phone")
t = threads.get("c_phone")
check(f"the memory pass saves facts and notes where the chat stands {saved and saved[0]['title']} {t}",
      saved and saved[0]["title"] == "wants-edge-60-pro" and t and t["topic"] == "Phone hunt"
      and t["open"] == ["Hasn't bought it yet"])
check("it is told what this chat had open before, to close what got done",
      "Open before (this chat)" in asked[0] and "\"thread\"" in asked[0])

# An extractor answering the old way (a bare list) still saves its facts.
saved2, _ = memory_extract.extract_and_save(
    [{"role": "user", "content": "my left earbud died"}, {"role": "assistant", "content": "Ugh."}],
    lambda s, u: json.dumps([{"title": "broken-earbud", "category": "user", "description": "Left earbud died.",
                              "body": "", "confidence": 4, "volatility": "transient"}]), chat_id="c_ear")
check("an answer as a bare list still saves its facts", saved2 and saved2[0]["title"] == "broken-earbud")

# Done items close; the next pass says so.
threads.note("c_phone", {"topic": "Phone hunt", "where": "Bought the Edge 60 Pro.", "open": [],
                         "closed": ["Hasn't bought it yet"]})
check("an item that got done is closed", threads.get("c_phone")["open"] == [])
threads.note("c_phone", {"topic": "Phone hunt", "where": "Still looking for the Edge 60 Pro.", "open": ["Hasn't bought it yet"]},
             now=now - 2 * DAY)
threads.note("c_cbse", {"topic": "CBSE private form", "where": "Form submitted, documents uploaded.",
                        "open": ["Fee payment"]}, now=now - 1 * DAY)
threads.note("c_old", {"topic": "Old trip", "where": "Went to Goa.", "open": []}, now=now - 40 * DAY)
threads.note("c_here", {"topic": "This chat", "where": "Talking now.", "open": []}, now=now)

block = threads.block(exclude="c_here", now=now)
check(f"the block names recent threads and what's still open, newest and open ones first\n{block}",
      "CBSE private form (yesterday)" in block and "Still open: Fee payment" in block
      and "Phone hunt (2 days ago)" in block and block.index("CBSE") < block.index("Phone hunt"))
check("a thread from weeks ago and the chat it's being said in stay out", "Old trip" not in block and "This chat" not in block)
check(f"the block stays small ({len(block)} chars)", len(block) <= threads.MAX_CHARS)

# ---- the engine: the block goes in front of the message, not into history
memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"


def chunk(content=None, finish=None):
    return NS(choices=[NS(index=0, delta=NS(content=content, reasoning_content=None, model_extra={},
                                             tool_calls=None), finish_reason=finish)], usage=None, model_extra={})


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()


SENT = []


async def fake(client, **kw):
    SENT.append([dict(m) for m in kw.get("messages") or []])
    return Stream([chunk(content="The Edge 60 Pro?"), chunk(finish="stop")])
headless.chat_create = fake
got = {}
history = [{"role": "user", "content": "hey"}, {"role": "assistant", "content": "hey!"}]
asyncio.run(headless.run_once("yo! I got something, take a guess", model="m", history=history, chat_id="c_here",
                              permission_check=lambda *a: "allow", emit=lambda k, **f: got.setdefault(k, f)))
last = SENT[0][-1]["content"] if SENT else ""

check("the message goes to the model with what's been going on in front of it",
      last.startswith("<lately>") and "CBSE private form" in last and last.rstrip().endswith("]")
      and "yo! I got something" in last)
check("the history before it is sent as it was (the cached prefix holds)",
      [m.get("content") for m in SENT[0] if m.get("role") != "system"][:2] == ["hey", "hey!"])
check(f"the surface hears which threads went in {got.get('context_used')}",
      [r["topic"] for r in (got.get("context_used") or {}).get("threads", [])][:1] == ["CBSE private form"])

# Switched off in Settings: nothing goes in.
with open(os.path.join(work, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"auto_memory": False}, f)
SENT.clear()
asyncio.run(headless.run_once("guess", model="m", chat_id="c_here", permission_check=lambda *a: "allow",
                              emit=lambda k, **f: None))
check("with remembering switched off, nothing goes in", SENT and "<lately>" not in SENT[0][-1]["content"])
os.remove(os.path.join(work, "settings.json"))

# ---- an agent brain gets it too, and its turn runs the memory pass
from hearth import claude_brain  # noqa: E402
seen = {}


async def fake_turn(prompt, **kw):
    seen["prompt"], seen["context"] = prompt, kw.get("context")
    kw["emit"]("done", reply="The CBSE fee?", iterations=1)
    return 0
claude_brain.active = lambda: True
claude_brain.run_turn = fake_turn
noted = []
headless.note_turn_async = lambda msgs, model, chat_id, emit=None: noted.append((msgs[-1]["content"], chat_id))
asyncio.run(headless.run_once("I got something", chat_id="c_here", emit=lambda k, **f: None))
check(f"an agent brain is handed the same lines {str(seen.get('context'))[:60]!r}",
      seen.get("prompt") == "I got something" and "CBSE private form" in (seen.get("context") or ""))
check(f"and its turn runs the memory pass, with the reply and the chat {noted}",
      noted == [("The CBSE fee?", "c_here")])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all lately checks passed")
