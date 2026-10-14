"""Memory reaches the model the way the model asks for it.

Every message used to get memories attached by keyword. "you still here?"
pulled in a history-chapter note (it contained "still" and "here"), and the
block read as if the person had pasted it. The index of memories is in the
system prompt and the model recalls what it needs, so nothing is attached to
the person's words. The background saver can be switched off.

    .\\.venv\\Scripts\\python.exe tests\\test_recall_relevance.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-recall-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import headless, memory, memory_extract                  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


memory.save("History chapter notes", "project", "notes for the history chapter",
            "Minoans, Shang China, cuneiform. Still need the bronze age part; the timeline is here.")

SENT = []


class Stream:
    def __aiter__(self):
        async def gen():
            yield NS(choices=[NS(index=0, delta=NS(content="Yep.", reasoning_content=None, tool_calls=None,
                                                    model_extra={}), finish_reason="stop")],
                     usage=None, model_extra={})
        return gen()


async def fake(client, **kw):
    SENT.append(kw.get("messages"))
    return Stream()

headless.chat_create = fake
asyncio.run(headless.run_once("you still here?", model="m", emit=lambda k, **f: None))
last = [m for m in SENT[0] if m.get("role") == "user"][-1]["content"]
check(f"nothing from memory is attached to the person's message {last[-120:]!r}", "<memory>" not in last
      and "Minoans" not in last)

json.dump({"auto_memory": False}, open(os.path.join(work, "settings.json"), "w"))
check("the background saver can be switched off", memory_extract.enabled() is False)
json.dump({}, open(os.path.join(work, "settings.json"), "w"))
check("and is on unless switched off", memory_extract.enabled() is True)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all recall checks passed")
