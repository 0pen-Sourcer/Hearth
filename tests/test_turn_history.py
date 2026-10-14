"""What a turn ran stays in the history for the next turn.

Surfaces kept only each reply's text, so on the next turn the model saw an
answer with no tool behind it and told the person it had "never called a
tool". The engine now hands back the turn's tool calls and results, and takes
them in history. Stand-in model calls; one real read-only tool.

    .\\.venv\\Scripts\\python.exe tests\\test_turn_history.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-turnhist-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import headless, memory_extract                          # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"          # a cloud key: native tool calls, not the local text form
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


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


SENT = []


def make(replies):
    async def fake(client, **kw):
        SENT.append(kw.get("messages"))
        return Stream(replies.pop(0))
    return fake


call = NS(index=0, id="call_1", type="function",
          function=NS(name="system_info", arguments="{}"))
headless.chat_create = make([[chunk(tool=call, finish="tool_calls")],
                             [chunk(content="Your PC runs Windows."), chunk(finish="stop")]])
got = {}
asyncio.run(headless.run_once("what is my PC?", model="m", permission_check=lambda *a: "allow",
                              emit=lambda k, **f: got.setdefault(k, f)))
kept = (got.get("turn_messages") or {}).get("messages") or []
check(f"the turn hands back its tool call, the tool's result and the answer {[m['role'] for m in kept]}",
      [m["role"] for m in kept] == ["assistant", "tool", "assistant"]
      and kept[0].get("tool_calls") and kept[1].get("tool_call_id") == "call_1"
      and kept[2].get("content") == "Your PC runs Windows.")

SENT.clear()
headless.chat_create = make([[chunk(content="Yes, I ran system_info."), chunk(finish="stop")]])
history = [{"role": "user", "content": "what is my PC?"}] + kept
asyncio.run(headless.run_once("did you check?", model="m", history=history, emit=lambda k, **f: None))
seen = SENT[0]
check("the next turn's history sends the call and its result to the model",
      any(m.get("tool_calls") for m in seen) and any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1"
                                                     for m in seen))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all turn history checks passed")
