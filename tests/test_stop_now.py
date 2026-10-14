"""Stop ends a reply now, on every surface.

The engine only looked at stop between model calls, so a long reply, or a
long prompt read on a local model that sends nothing back for a minute, ran
to its end after stop was pressed. Stand-in model calls that never finish on
their own.

    .\\.venv\\Scripts\\python.exe tests\\test_stop_now.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-stopnow-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import headless, memory_extract                          # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Endless:
    """A reply that streams one word and then never another."""
    closed = False

    def __aiter__(self):
        async def gen():
            yield NS(choices=[NS(index=0, delta=NS(content="Thinking about", reasoning_content=None,
                                                    tool_calls=None, model_extra={}), finish_reason=None)],
                     usage=None, model_extra={})
            while not self.closed:
                await asyncio.sleep(0.05)
            raise RuntimeError("stream closed")
        return gen()

    async def close(self):
        self.closed = True


def run(make_call, stop_after):
    """Press stop `stop_after` seconds into the model call; how long until the turn says stopped."""
    stop = threading.Event()
    at = {}

    async def fake_create(client, **kw):
        threading.Timer(stop_after, lambda: (at.setdefault("stop", time.time()), stop.set())).start()
        return await make_call()
    headless.chat_create = fake_create
    events = []

    def emit(kind, **f):
        events.append(kind)
        if kind == "cancelled":
            at["cancelled"] = time.time()

    async def go():
        await asyncio.wait_for(headless.run_once("hi", emit=emit, model="m", should_cancel=stop.is_set), timeout=20)
    try:
        asyncio.run(go())
    except asyncio.TimeoutError:
        events.append("timed out")
    late = at["cancelled"] - at["stop"] if "cancelled" in at and "stop" in at else 99.0
    return late, events


async def mid_reply():
    return Endless()


async def reading_prompt():
    await asyncio.sleep(3600)          # a local model reading a long prompt


late, ev = run(mid_reply, 1.0)
check(f"stop part-way through a reply ends it within a second ({late:.1f}s) {ev[-3:]}",
      late < 1.5 and "cancelled" in ev and "error" not in ev)
late, ev = run(reading_prompt, 1.0)
check(f"stop while the model is still reading the prompt ends it within a second ({late:.1f}s) {ev[-3:]}",
      late < 1.5 and "cancelled" in ev and "error" not in ev)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all stop checks passed")
