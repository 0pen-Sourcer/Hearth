"""A provider refusing one request field doesn't stop every message.

Anthropic's newest models began refusing `temperature`, and every message to
them failed with "rejected a request parameter" while the reason sat unread in
the provider's reply. A field the provider names as refused is now dropped,
remembered for that model and the call made again; anything else still fails,
and the error shown carries the provider's own words.

    .\\.venv\\Scripts\\python.exe tests\\test_chat_call.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import chat_call  # noqa: E402
from hearth.errors import classify_api_error  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Refused(Exception):
    def __init__(self, msg):
        super().__init__(f"Error code: 400 - {{'error': {{'message': '{msg}', 'type': 'invalid_request_error'}}}}")
        self.body = {"error": {"message": msg}}
        self.status_code = 400


def provider(refuses):
    sent = []

    def create(**kw):
        sent.append(dict(kw))
        for field, msg in refuses.items():
            if field in kw or field in (kw.get("extra_body") or {}):
                raise Refused(msg)
        return "reply"
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))), sent


client, sent = provider({"temperature": "`temperature` is deprecated for this model."})
got = chat_call.chat_create(client, model="claude-new", messages=[], temperature=0.7, stream=True)
check("a field the provider says it won't take is dropped and the call made again",
      got == "reply" and len(sent) == 2 and "temperature" not in sent[1] and sent[1]["stream"] is True)
sent.clear()
chat_call.chat_create(client, model="claude-new", messages=[], temperature=0.7)
check("and it is not sent to that model again", len(sent) == 1 and "temperature" not in sent[0])

client, sent = provider({"repeat_penalty": "Unrecognized request argument supplied: repeat_penalty"})
check("a refused field inside extra_body is dropped too",
      chat_call.chat_create(client, model="m2", messages=[], extra_body={"repeat_penalty": 1.1, "top_k": 4}) == "reply"
      and sent[-1]["extra_body"] == {"top_k": 4})

client, sent = provider({"tools": "this model does not support tools"})
try:
    chat_call.chat_create(client, model="m3", messages=[], tools=[{"x": 1}])
    kept = False
except Refused:
    kept = True
check("the tools themselves are never dropped quietly", kept and "tools" in sent[-1])


class AsyncClient:
    def __init__(self):
        self.sent = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.sent.append(dict(kw))
        if "max_tokens" in kw:
            raise Refused("max_tokens is not supported with this model. Use max_completion_tokens instead.")
        return "async reply"


AsyncClient.__name__ = "AsyncFake"
ac = AsyncClient()
check("the same for the async client",
      asyncio.run(chat_call.chat_create(ac, model="m4", messages=[], max_tokens=10)) == "async reply"
      and "max_tokens" not in ac.sent[-1])

info = classify_api_error(Refused("`top_p` and `temperature` cannot both be set"), False)
check(f"a refused request says why, in the provider's words: {info.hint!r}",
      "cannot both be set" in info.hint)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all chat call checks passed")
