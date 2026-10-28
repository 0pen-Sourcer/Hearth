"""The reply so far reaches the phone often, and its last piece always does.

Live text went at most every 0.35 s and reasoning every 0.5 s, and a piece that
arrived inside that wait was dropped: when the model then paused (a tool, a
long think), the phone sat on an old piece until the next one. Now live text
goes every 0.1 s, reasoning every 0.25 s, the newest piece goes when the wait
ends, and nothing paced is sent after the finished reply.

    .\\.venv\\Scripts\\python.exe tests\\test_relay_pacing.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-pacing-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

from hearth import channel_core as cc, relay_bridge as RB          # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


sent: list = []
r = RB.Relay(asyncio.new_event_loop())
r._push = lambda chat, obj: sent.append(dict(obj, at=time.time(), t=cc.turn_context().get("t"))) or "id"
r.finished = lambda *a, **k: None
cc._TURN.ctx = {"c": "chat1", "t": "turn1"}

check("live text goes ten times a second, reasoning four", RB.LIVE_EVERY <= 0.1 and RB.THINK_EVERY <= 0.25)
t0 = time.time()
r.live("room", "Hel")
r.live("room", "Hello")               # inside the wait
r.live("room", "Hello the")           # inside the wait: this one is the newest
time.sleep(0.3)                       # the model pauses
lives = [s for s in sent if s["type"] == "live"]
check(f"the first piece goes at once and the newest one when the wait ends, nothing in between {[s['text'] for s in lives]}",
      [s["text"] for s in lives] == ["Hel", "Hello the"])
check("the waiting piece keeps its turn", all(s["t"] == "turn1" for s in lives))
check(f"the newest piece came within the pacing time ({lives[-1]['at'] - t0:.2f} s)", lives[-1]["at"] - t0 < 0.25)

sent.clear()
r.live("room", "Hello there, and")
r.live("room", "Hello there, and more")      # waits
r.send("room", "Hello there, and more words.")
time.sleep(0.3)
check(f"nothing paced is sent after the finished reply {[s['type'] for s in sent]}",
      [s["type"] for s in sent] == ["live", "text"])

sent.clear()
cc._TURN.ctx = {"c": "chat1", "t": "turn2"}
r.think("room", "a")
r.think("room", "ab")
time.sleep(0.4)
check(f"reasoning goes the same way {[s['text'] for s in sent]}", [s["text"] for s in sent] == ["a", "ab"])

print("\n" + ("all pacing checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
