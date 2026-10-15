"""Every provider brain gets its turn priced, not only one vendor.

Each provider reports usage in its own shape: OpenAI and xAI put cached input
under prompt_tokens_details, Gemini and Groq report plain counts, OpenRouter
adds what the call cost. A turn of two calls must add both up and price them
under the right provider's catalog entry (Hearth's "gemini" is the catalog's
"google", "grok" is "xai").

    .\\.venv\\Scripts\\python.exe tests\\test_turn_cost_providers.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-costprov-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
sys.path.insert(0, ROOT)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    f.write('{"auto_memory": false}')       # no background saver calling out after each turn

from hearth import headless, model_facts  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def chunk(content=None, tool=None, finish=None):
    return NS(choices=[NS(index=0, delta=NS(content=content, reasoning_content=None, model_extra={},
                                             tool_calls=[tool] if tool else None), finish_reason=finish)],
              usage=None, model_extra={})


def usage_chunk(u):
    return NS(choices=[], usage=u, model_extra={})


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()


call = NS(index=0, id="call_1", type="function", function=NS(name="system_info", arguments="{}"))
asked = []
model_facts.catalog_entry = lambda pid, mid: (asked.append((model_facts._CATALOG_ID.get(pid, pid), mid))
                                              or {"cost": {"input": 1.0, "output": 2.0, "cache_read": 0.1}})

SHAPES = {
    "openai": ("https://api.openai.com/v1", "google",
               lambda p, c: NS(prompt_tokens=p, completion_tokens=c, prompt_tokens_details=NS(cached_tokens=p // 2), model_extra={})),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "google",
               lambda p, c: NS(prompt_tokens=p, completion_tokens=c, model_extra={})),
    "grok": ("https://api.x.ai/v1", "xai",
             lambda p, c: NS(prompt_tokens=p, completion_tokens=c, prompt_tokens_details=NS(cached_tokens=0), model_extra={})),
    "groq": ("https://api.groq.com/openai/v1", "groq",
             lambda p, c: NS(prompt_tokens=p, completion_tokens=c, model_extra={})),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter",
                   lambda p, c: NS(prompt_tokens=p, completion_tokens=c, model_extra={"cost": 0.0005})),
}
for pid, (base, _cat, shape) in SHAPES.items():
    asked.clear()
    headless.LOCAL_API_BASE = base
    replies = [[chunk(tool=call, finish="tool_calls"), usage_chunk(shape(1000, 100))],
               [chunk(content="Done."), chunk(finish="stop"), usage_chunk(shape(1200, 50))]]

    async def fake(client, **kw):
        return Stream(replies.pop(0))
    headless.chat_create = fake
    got = {}
    asyncio.run(headless.run_once("what is my PC?", model="some-model", permission_check=lambda *a: "allow",
                                  emit=lambda k, **f: got.update(f.get("stats") or {}) if k == "done" else None))
    want = {"openai": (1100 * 1.0 + 1100 * 0.1 + 150 * 2.0) / 1e6, "openrouter": 0.001}.get(pid, (2200 * 1.0 + 150 * 2.0) / 1e6)
    check(f"{pid}: both calls counted and priced ({got.get('tokens_in')} in, {got.get('tokens_out')} out, "
          f"${got.get('cost_usd')}, asked {asked[:1]})",
          got.get("tokens_in") == 2200 and got.get("tokens_out") == 150
          and abs((got.get("cost_usd") or 0) - want) < 1e-9
          and (pid == "openrouter" or (asked and asked[0][0] == {"openai": "openai"}.get(pid, SHAPES[pid][1]))))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all provider cost checks passed")
