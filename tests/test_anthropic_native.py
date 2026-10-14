"""Anthropic as the brain goes through its own Messages API.

Its OpenAI-compatible endpoint takes `reasoning_effort` and ignores it, and
refuses thinking, so the think button did nothing on the Anthropic API. A
stand-in for the SDK plays a tool round with thinking, then an answer.

    .\\.venv\\Scripts\\python.exe tests\\test_anthropic_native.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace as NS

work = tempfile.mkdtemp(prefix="hearth-anthnative-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import headless, memory_extract                          # noqa: E402
memory_extract.extract_and_save = lambda *a, **k: ([], [])          # no background call with the fake key

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from hearth import anthropic_chat as A
except ImportError:
    A = None
check("there is a native Anthropic path", A is not None and A.available())
if A is None:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)


class Block(NS):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items() if v is not None}


SENT = []
ROUNDS = [
    # A tool round with thinking, then the answer.
    ([NS(type="content_block_start", index=0, content_block=NS(type="thinking")),
      NS(type="content_block_delta", index=0, delta=NS(type="thinking_delta", thinking="Check the PC first.")),
      NS(type="content_block_start", index=1, content_block=NS(type="tool_use", id="toolu_1", name="system_info")),
      NS(type="content_block_delta", index=1, delta=NS(type="input_json_delta", partial_json="{}"))],
     [Block(type="thinking", thinking="Check the PC first.", signature="sig1"),
      Block(type="tool_use", id="toolu_1", name="system_info", input={})], "tool_use"),
    ([NS(type="content_block_start", index=0, content_block=NS(type="text")),
      NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text="All good."))],
     [Block(type="text", text="All good.")], "end_turn"),
]


class FakeStream:
    def __init__(self, params):
        SENT.append(params)
        self.refuse = params.get("thinking", {}).get("type") == "disabled"
        self.events, blocks, stop = ROUNDS[min(len(SENT) - 1, 1)]
        self.final = NS(content=blocks, stop_reason=stop, stop_details=None,
                        usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=50,
                                 cache_creation_input_tokens=0))

    async def __aenter__(self):
        # The SDK sends the request here; a refused field is a 400 at this point.
        if self.refuse:
            raise A.anthropic.BadRequestError("thinking.type disabled is not supported on this model; use between_tools")
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()

    async def get_final_message(self):
        return self.final


class FakeSDK:
    def __init__(self, **kw):
        self.beta = NS(messages=NS(stream=lambda **p: FakeStream(p)))


A.anthropic.AsyncAnthropic = FakeSDK
A.anthropic.BadRequestError.__init__ = lambda self, message, response=None, body=None: Exception.__init__(self, message)
headless.LOCAL_API_BASE = "https://api.anthropic.com/v1/"
headless.LOCAL_API_KEY = "sk-test"


def turn(think, level):
    ev = {"thinking": "", "text": "", "tools": [], "errors": []}

    def emit(kind, **kw):
        if kind == "thinking_chunk":
            ev["thinking"] += kw.get("content") or ""
        elif kind == "assistant":
            ev["text"] = kw.get("content") or ""
        elif kind == "tool_call":
            ev["tools"].append(kw.get("name"))
        elif kind == "error":
            ev["errors"].append(kw.get("message"))
    asyncio.run(headless.run_once("how is my PC?", emit=emit, model="claude-x", think=think, think_level=level,
                                  permission_check=lambda *_a: "allow"))
    return ev


ev = turn(True, "high")
first, second = SENT[0], SENT[-1]
check(f"thinking is asked for and shown, at the effort picked {first.get('thinking')} {first.get('output_config')}",
      first.get("thinking", {}).get("type") == "adaptive" and first.get("thinking", {}).get("display") == "summarized"
      and first.get("output_config") == {"effort": "high"} and ev["thinking"] == "Check the PC first.")
check(f"the tool call runs and the answer comes back {ev['tools']} {ev['text']!r} {ev['errors']}",
      ev["tools"] == ["system_info"] and ev["text"] == "All good." and not ev["errors"])
asst = [m for m in second["messages"] if m["role"] == "assistant"][-1]["content"]
check(f"the tool round goes back with its thinking unchanged {[b.get('type') for b in asst]}",
      [b.get("type") for b in asst] == ["thinking", "tool_use"] and asst[0].get("signature") == "sig1")
res = [m for m in second["messages"] if m["role"] == "user"][-1]["content"]
check("the tool's result goes back as a tool result for that call",
      res and res[-1].get("type") == "tool_result" and res[-1].get("tool_use_id") == "toolu_1")
check("a block the API can't match any more is dropped rather than failing the request",
      first["thinking"].get("block_binding") == {"prefix_mismatch_behavior": "drop_block"})

# Esc in the terminal closes the stream mid-reply.
closed = []
FakeStream.__aexit__ = lambda self, *a: (closed.append(1), _done())[1]
async def _done():
    return False
async def _esc():
    st = await A.AsyncAnthropicChat(api_key="k").chat.completions.create(
        model="claude-x", messages=[{"role": "user", "content": "hi"}], stream=True, hearth_think=True)
    await st.close()
asyncio.run(_esc())
check("a reply can be stopped part-way, ending the request", closed == [1])

SENT.clear()
A._REFUSED.clear()
turn(False, "")
check(f"think off sends the model's own off; a model that refuses 'disabled' gets between_tools {[p.get('thinking') for p in SENT]}",
      SENT[0].get("thinking") == {"type": "disabled"} and SENT[1].get("thinking") == {"type": "between_tools"})

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all native Anthropic checks passed")
