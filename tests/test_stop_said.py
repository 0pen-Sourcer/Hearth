"""A stop typed or said while a turn runs halts it, like the Stop button.

"No, don't do it" sent while the brain was working only joined its queue: the
steps it had already asked for still ran, it was handed its tools again, and a
phone or chat app had no Stop button at all. Now the turn reads what arrives
each time it checks for Stop: a stop holds back every step not yet run, cuts
the reply being written, and the brain answers with no tools. Stand-in model
calls and a stand-in tool; nothing runs on this PC.

    .\\.venv\\Scripts\\python.exe tests\\test_stop_said.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import headless, memory_extract                          # noqa: E402
from hearth.stop_words import is_stop                                 # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"          # tools go as the API's own field, so a request shows them
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


# ---------------------------------------------------------------- what counts as a stop
for said in ["No, don't do it. What the hell are you doing?", "Terminate whatever you did.",
             "Okay, no need to talk about all of that. You can stop now.", "stop", "Stop!", "no no no",
             "wait", "Cancel that", "hold on", "Okay wait, stop it", "don't run that"]:
    check(f"a stop: {said!r}", is_stop(said))
for said in ["No, no, no, I was saying open the inglen account.",
             "Yeah go ahead it's the second profile after the cinematic boom one.",
             "Cancel my 3pm meeting", "stop by the shop on the way home", "Wait are you repeating stuff.",
             "Okay, okay, goodbye, goodbye.", "don't forget the second file", "open chrome"]:
    check(f"not a stop: {said!r}", not is_stop(said))


# ---------------------------------------------------------------- the turn
def chunk(**delta):
    d = dict(content=None, reasoning_content=None, tool_calls=None, model_extra={})
    d.update(delta)
    return NS(choices=[NS(index=0, delta=NS(**d), finish_reason=None)], usage=None, model_extra={})


class Stream:
    def __init__(self, pieces, hang=False):
        self.pieces, self.hang, self.closed = pieces, hang, False

    def __aiter__(self):
        async def gen():
            for p in self.pieces:
                yield p
            while self.hang and not self.closed:
                await asyncio.sleep(0.05)
            if self.hang:
                raise RuntimeError("stream closed")
        return gen()

    async def close(self):
        self.closed = True


def two_steps():
    calls = [NS(index=i, id=f"c{i}", type="function",
                function=NS(name="read_file", arguments='{"path": "step%d.txt"}' % i)) for i in (1, 2)]
    return Stream([chunk(tool_calls=calls)])


def run(first, steer_at):
    """One turn: the model's first reply is `first`; `steer_at(queue, ran)` puts their words in."""
    queue, ran, requests, events = [], [], [], []

    def fake_tool(name, args):
        ran.append(args.get("path"))
        steer_at(queue, ran)
        return "contents"
    headless.execute_tool = fake_tool

    async def fake_create(client, **kw):
        requests.append({"tools": bool(kw.get("tools")), "last": kw["messages"][-1]["content"],
                         "at": time.time()})
        if len(requests) == 1:
            return first()
        return Stream([chunk(content="Stopped. I had opened step one; nothing else ran.")])
    headless.chat_create = fake_create

    def drain():
        out, queue[:] = list(queue), []
        return out

    async def go():
        await asyncio.wait_for(headless.run_once("do both steps", emit=lambda k, **f: events.append((k, f)),
                                                 model="m", drain_steer=drain), timeout=20)
    t0 = time.time()
    asyncio.run(go())
    return ran, requests, events, t0


# A stop that comes in while the first step runs: the second never runs.
ran, req, ev, _ = run(two_steps, lambda q, ran: q.append("No, don't do it.") if len(ran) == 1 else None)
check(f"the step after the stop doesn't run ({ran})", ran == ["step1.txt"])
check("the brain answers with no tools after a stop", len(req) == 2 and not req[1]["tools"])
check("and is told it was stopped", len(req) == 2 and "told you to stop" in str(req[1]["last"]))
done = [f for k, f in ev if k == "done"]
check(f"the turn ends as finished, with the answer ({done[-1].get('reason') if done else None})",
      done and done[-1].get("reason") == "finished")


# A stop typed while the model is still writing: the reply is cut and nothing it asked for runs.
def writing():
    calls = [NS(index=0, id="c1", type="function", function=NS(name="read_file", arguments='{"path": "x.txt"}'))]
    return Stream([chunk(content="Opening it now"), chunk(tool_calls=calls)], hang=True)


box = {}


def say_stop_soon():
    def put():
        box["q"].append("stop")
        box["at"] = time.time()
    threading.Timer(1.0, put).start()
    return writing()


def run_mid():
    queue, ran, requests = [], [], []
    box["q"] = queue
    headless.execute_tool = lambda n, a: ran.append(a.get("path")) or "x"

    async def fake_create(client, **kw):
        requests.append({"tools": bool(kw.get("tools")), "at": time.time()})
        if len(requests) == 1:
            return say_stop_soon()
        return Stream([chunk(content="Stopped.")])
    headless.chat_create = fake_create

    def drain():
        out, queue[:] = list(queue), []
        return out

    async def go():
        await asyncio.wait_for(headless.run_once("open it", emit=lambda k, **f: None, model="m",
                                                 drain_steer=drain), timeout=8)
    try:
        asyncio.run(go())
    except asyncio.TimeoutError:
        pass                                       # never stopped: the checks below say so
    return ran, requests


ran, req = run_mid()
late = (req[1]["at"] - box["at"]) if len(req) > 1 and "at" in box else 99
check(f"a stop typed mid-reply cuts it within a second ({late:.1f}s)", late < 1.5)
check(f"and what it was about to do never runs ({ran})", ran == [])
check("the answer after it has no tools", len(req) > 1 and not req[1]["tools"])

# Words that aren't a stop still steer, as before: the next step runs.
ran, req, ev, _ = run(two_steps, lambda q, ran: q.append("also check the third one") if len(ran) == 1 else None)
check(f"a message that isn't a stop doesn't halt the steps ({ran})", ran == ["step1.txt", "step2.txt"])
check(f"and the brain keeps its tools ({[r['tools'] for r in req]}, {[k for k, f in ev if k == 'nudge']})",
      len(req) >= 2 and req[1]["tools"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all stop-said checks passed")
