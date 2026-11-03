"""At the step limit the brain says what it found and what's left; Stop reads as stopped.

A long computer-use task ran out of steps mid-sentence: the last thing in the
chat was half a plan, and the person never heard what was found. Now one more
reply, with no tools, closes the turn. And Stop, which ended the loop by the
same door, was logged as running out of steps. Stand-in model calls and tools.

    .\\.venv\\Scripts\\python.exe tests\\test_step_limit.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402,F401
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from hearth import headless, memory_extract                          # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"
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


def turn(always_tools: bool, stop_after=None, max_depth=3):
    requests, ran, events = [], [], []
    stop = threading.Event()

    def tool(name, args):
        ran.append(args.get("path"))
        if stop_after and len(ran) >= stop_after:
            stop.set()
        return "ok"
    headless.execute_tool = tool

    async def fake_create(client, **kw):
        n = len(requests)
        requests.append(bool(kw.get("tools")))
        if kw.get("tools") or always_tools:
            call = NS(index=0, id=f"c{n}", type="function",
                      function=NS(name="read_file", arguments='{"path": "f%d.txt"}' % n))
            return Stream([chunk(content=f"Step {n}. "), chunk(tool_calls=[call])])
        return Stream([chunk(content="I found the version, drew the box; the text and the save are left.")])
    headless.chat_create = fake_create

    async def go():
        await asyncio.wait_for(headless.run_once("do the long task", emit=lambda k, **f: events.append((k, f)),
                                                 model="m", max_depth=max_depth, should_cancel=stop.is_set),
                               timeout=20)
    asyncio.run(go())
    return requests, ran, events


req, ran, ev = turn(always_tools=False)
done = [f for k, f in ev if k == "done"][-1]
said = "".join(f.get("content") or "" for k, f in ev if k == "assistant_chunk")
check(f"at the limit it gets one more reply, with no tools ({req})", req == [True, True, True, False])
check(f"and the steps before it ran ({ran})", ran == ["f0.txt", "f1.txt", "f2.txt"])
check("that reply says what it found and what's left", "what's left" in said or "are left" in said)
check(f"the turn says it hit the limit ({done.get('reason')})", done.get("reason") == "max_depth_reached")

req, ran, ev = turn(always_tools=True)
check(f"a last reply that still reaches for a tool doesn't run it ({ran})", ran == ["f0.txt", "f1.txt", "f2.txt"])

req, ran, ev = turn(always_tools=False, stop_after=1, max_depth=10)
done = [f for k, f in ev if k == "done"][-1]
check(f"Stop ends the turn as stopped, not out of steps ({done.get('reason')})", done.get("reason") == "stopped")
check(f"and nothing ran after it ({ran})", ran == ["f0.txt"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all step limit checks passed")
