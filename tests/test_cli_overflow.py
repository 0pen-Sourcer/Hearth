"""A prompt that overflows the context, in the terminal.

The terminal retried it twice unchanged after a short wait, which never helps:
the same prompt overflows again. One large tool result is enough to cause it.
It now shrinks the prompt before each retry, as the window already did.

    .\\.venv\\Scripts\\python.exe tests\\test_cli_overflow.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

os.environ["JARVIS_WORKSPACE"] = tempfile.mkdtemp(prefix="hearth-overflow-")
os.environ["JARVIS_NO_ONBOARDING"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import hearth_cli as C                                               # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


sent = []


class _Completions:
    async def create(self, **kw):
        size = sum(len(str(m.get("content") or "")) for m in kw["messages"])
        sent.append(size)
        if size > 30_000:
            raise RuntimeError("This model's maximum context length is 8192 tokens. "
                               f"However, your messages resulted in {size // 4} tokens.")
        return "stream"


cli = object.__new__(C.JarvisCLI)
cli.client = type("X", (), {"chat": type("Y", (), {"completions": _Completions()})()})()
cli.context_tokens = 8192
cli._no_reasoning_effort = set()
cli.current_model = "m"
messages = [
    {"role": "system", "content": "You are Hearth."},
    {"role": "user", "content": "read the log"},
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "c1", "type": "function",
        "function": {"name": "read_file", "arguments": "{\"path\": \"big.log\"}"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "x" * 100_000},
]
err = None
with redirect_stdout(io.StringIO()):
    try:
        got = asyncio.run(cli._open_stream({"model": "m", "messages": messages, "stream": True}))
    except Exception as e:
        got, err = None, e
check(f"an overflowing prompt is shrunk and then goes through (sizes sent: {sent})",
      got == "stream" and err is None)
check("the retry sent less, not the same prompt again", len(sent) >= 2 and sent[1] < sent[0])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all overflow checks passed")
