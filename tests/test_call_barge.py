"""Talking over the voice on a call cuts what it was saying, and only the reply
written after that is spoken.

What was heard used to reset how much of the running reply had been spoken, so
on a phone call each sentence said over it made the next piece of the same reply
start again from its first word: the same paragraph, over and over, while new
text arrived. Tapping the orb only went quiet until the next piece. Stand-ins
for the voice and the phone; nothing is played.

    .\\.venv\\Scripts\\python.exe tests\\test_call_barge.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import call as C                                          # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def make(said_back="joined"):
    sent, spoken, cuts = [], [], []
    c = C.Call(sent.append, lambda text: said_back, cid="c_call")
    c._speak = lambda text: spoken.append(text)
    c.speaker.stop = lambda: cuts.append(1)
    c._settle = lambda: None
    return c, spoken, cuts


FIRST = "Right, stopping, I'm sorry, I overstepped there. Here's the honest version of what happened. "
MORE = "I tried to open a fresh Chrome window. "
AFTER = "Okay, I'll stop here. "

# The phone's path: live() and done() carry no turn number (relay_bridge).
c, spoken, cuts = make()
c.begin()
c.live(FIRST)
c.live(FIRST + MORE)
before = list(spoken)
c._barge()                                     # they talk over it
c._heard("Wait, are you repeating stuff?")     # joins the reply still being written
c.live(FIRST + MORE + "And then")
c.live(FIRST + MORE + "And then I killed it. ")
again = [s for s in spoken[len(before):] if "overstepped" in s or "fresh Chrome" in s]
check(f"talking over it doesn't start the reply again ({spoken[len(before):]})", not again)
check("what it writes after they spoke is said", any("killed it" in s for s in spoken[len(before):]))
n = len(spoken)
c.done((FIRST + MORE + "And then I killed it. ").strip())
check(f"the finished reply isn't said again at its end ({spoken[n:]})", spoken[n:] == [])

# A sentence said over it, with nothing new written after: nothing more is said.
c, spoken, cuts = make()
c.begin()
c.live(FIRST)
c._barge()
c._heard("Are you repeating yourself?")
n = len(spoken)
c.done(FIRST.strip())
check(f"after a barge the old reply's end stays unsaid ({spoken[n:]})", spoken[n:] == [])

# The next turn is spoken whole.
c.begin()
c.live("Yeah, I kept circling the same thing. ")
check("a new reply after a barge is spoken from its start", any("circling" in s for s in spoken[n:]))

# Tapping the orb: the rest of this reply stays unsaid, also on the phone's path.
c, spoken, cuts = make()
c.begin()
c.live(FIRST)
n = len(spoken)
c.hush()
c.live(FIRST + MORE)
c.done((FIRST + MORE).strip())
check(f"after the orb is tapped the reply stays quiet ({spoken[n:]})", spoken[n:] == [])

# "Stop" said while nothing was playing still cuts the voice queued for the phone.
c, spoken, cuts = make()
c.begin()
c.live(FIRST)
c._heard("No, don't do it.")
check("a spoken stop cuts the voice", cuts)
n = len(spoken)
c.live(FIRST + MORE)
check(f"and what was written before it isn't said ({spoken[n:]})", spoken[n:] == [])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all call barge checks passed")
