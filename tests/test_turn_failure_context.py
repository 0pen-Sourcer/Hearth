"""A turn that failed reaches the brain on the next turn as context, never as
something it said: the chat file (terminal, chat apps, phone), the window's
history and the engine all carry it the same way. Stand-in model calls.

    .\\.venv\\Scripts\\python.exe tests\\test_turn_failure_context.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-failctx-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import chat_sync, headless, memory_extract  # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.LOCAL_API_BASE = "https://api.example.invalid/v1"
headless.LOCAL_API_KEY = "sk-test"
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


LIMIT = "Claude Code is out of usage: You've hit your session limit · resets 3:20pm."
saved = [
    {"role": "user", "content": "remind me to stretch at 5"},
    {"role": "assistant", "content": "", "events": [{"kind": "error", "text": LIMIT, "pos": 0}]},
    {"role": "user", "content": "did you set it?"},
    {"role": "assistant", "content": "Not yet, the last try didn't go through."},
    {"role": "user", "content": "try now"},
    {"role": "assistant", "content": "", "events": [{"kind": "error", "text": LIMIT, "pos": 0}]},
]
h = chat_sync.from_chat(saved)
check("the failed reply isn't in the history as words the model said",
      not any(m["role"] == "assistant" and "out of usage" in m["content"] for m in h))
check(f"the next message carries it as context ({h[1]['content'][:70]!r})",
      h[1]["role"] == "user" and h[1]["content"].startswith("<previous_turn_failed>")
      and "resets 3:20pm" in h[1]["content"] and h[1]["content"].endswith("did you set it?"))
check("a message after a reply that worked carries nothing",
      not h[-1]["content"].startswith("<previous_turn_failed>"))
check("one at the end is given for the coming turn",
      getattr(chat_sync, "last_failure", lambda m: "")(saved) == LIMIT)
check("and none when the last reply worked", getattr(chat_sync, "last_failure", lambda m: "x")(saved[:4]) == "")


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
    SENT.append(kw.get("messages"))
    return Stream([chunk(content="Setting it now."), chunk(finish="stop")])

headless.chat_create = fake
kw = {"prior_failure": LIMIT} if "prior_failure" in headless._run_once.__code__.co_varnames else {}
EV = []
asyncio.run(headless.run_once("try now", emit=lambda k, **f: EV.append((k, str(f)[:120])), model="stand-in", history=h[:4], **kw))

last_user = next((m for m in reversed(SENT[0] if SENT else []) if m.get("role") == "user"), {})
text = last_user.get("content") if isinstance(last_user.get("content"), str) else str(last_user.get("content"))
check(f"the engine puts it in front of this turn's message ({text[:80]!r})",
      "<previous_turn_failed>" in text and "resets 3:20pm" in text
      and text.index("<previous_turn_failed>") < text.index("try now"))
check("not as an assistant turn", not any(m.get("role") == "assistant" and "out of usage" in str(m.get("content"))
                                          for m in (SENT[0] if SENT else [])))
check("long errors are cut to their first line", "\n" not in headless.failure_note("a\nTraceback ...")
      .split("</previous_turn_failed>")[0] if hasattr(headless, "failure_note") else False)

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall turn failure context checks passed")
