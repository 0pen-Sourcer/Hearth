"""However a turn ends, the model is released and the memory pass can run.

A turn that hit the step limit, stopped on an error or called end_session left
the "a turn is running" flag set, so the background memory pass skipped every
later turn and a due reminder waited up to ten minutes for a turn that was over.
The step-limit exit also never ran the memory pass. Stand-in model calls.

    .\\.venv\\Scripts\\python.exe tests\\test_turn_release.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-release-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import headless, memory_extract  # noqa: E402

headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ran = []
memory_extract.extract_and_save = lambda msgs, llm, **kw: (ran.append(kw.get("chat_id")) or ([], []))


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


# 1. The model call blows up: the turn ends, and the model is free again.
async def boom(client, **kw):
    raise RuntimeError("the server fell over")
headless.chat_create = boom
try:
    asyncio.run(headless.run_once("hi", model="m", emit=lambda k, **f: None, permission_check=lambda *a: "allow"))
except Exception:
    pass
check("a turn that ends on an error leaves the model free", not headless.turn_is_active())

# 2. The step limit: it keeps calling a tool until the cap.
n = {"i": 0}


async def loop(client, **kw):
    n["i"] += 1
    call = NS(index=0, id=f"c{n['i']}", type="function", function=NS(name="system_info", arguments="{}"))
    return Stream([chunk(tool=call, finish="tool_calls")])
headless.chat_create = loop
ran.clear()
got = {}
asyncio.run(headless.run_once("check my pc", model="m", max_depth=2, chat_id="c_cap",
                              emit=lambda k, **f: got.setdefault(k, f), permission_check=lambda *a: "allow"))
check(f"a turn that hits the step limit ends as one ({(got.get('done') or {}).get('reason')})",
      (got.get("done") or {}).get("reason") == "max_depth_reached")
check("and leaves the model free", not headless.turn_is_active())
for _ in range(40):
    if ran:
        break
    time.sleep(0.1)
check(f"and the memory pass runs for it, with its chat ({ran})", ran == ["c_cap"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all turn release checks passed")
