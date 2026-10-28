"""A provider out until a time isn't asked again before then.

Agent brains skipped a turn their plan was known to be out for; a provider on
the person's key was asked every time and failed every time. Now a quota or
rate-limit answer with a reset time is kept per provider and key
(provider_limits), and a turn before that time says "Groq is out of usage
until 4:30 PM" without calling; a call that goes through clears it.

    .\\.venv\\Scripts\\python.exe tests\\test_provider_limits.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-limits-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.environ["HEARTH_API_RETRIES"] = "0"
os.makedirs(os.environ["HEARTH_WORKSPACE"])

from hearth import provider_limits as L                             # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


now = 1_800_000_000.0
check("Retry-After in seconds", L._seconds("30", now) == now + 30)
check("OpenAI and Groq durations", L._seconds("1m30s", now) == now + 90 and L._seconds("6ms", now) == now + 0.006
      and abs(L._seconds("2m59.56s", now) - (now + 179.56)) < 1e-6)
from datetime import datetime, timezone                               # noqa: E402
check("Anthropic's RFC 3339 time", L._seconds("2027-01-15T10:00:00Z", now)
      == datetime(2027, 1, 15, 10, tzinfo=timezone.utc).timestamp())
check("OpenRouter's Unix milliseconds", L._seconds("1800000600000", now) == now + 600)
check("an HTTP date", L._seconds("Fri, 15 Jan 2027 10:00:00 GMT", now) is not None)


class E(Exception):
    def __init__(self, msg, headers, code=429):
        super().__init__(msg)
        self.status_code = code
        self.response = NS(headers=headers)


e = E("Error code: 429 - rate limit", {"retry-after": "20", "x-ratelimit-reset-tokens": "1h"})
check("the latest reset the answer names is kept", L.reset_at(e, now) == now + 3600)
check("a reset said to be weeks off isn't believed", L.reset_at(E("x", {"retry-after": str(40 * 86400)}), now) is None)
check("an answer without a time keeps nothing", L.note("https://api.groq.com/openai/v1", "gsk_1", "quota", "", E("x", {}), now) is None)

# ---- through a real turn
from hearth import headless, memory_extract                         # noqa: E402

memory_extract.extract_and_save = lambda *a, **k: ([], [])
headless.note_turn_async = lambda *a, **k: None
headless.LOCAL_API_BASE = "https://api.groq.com/openai/v1"
headless.LOCAL_API_KEY = "gsk_secret_key_1234567890"
calls = []
mode = {"fail": True}


class Stream:
    def __aiter__(self):
        async def gen():
            yield NS(choices=[NS(index=0, delta=NS(content="Hi.", reasoning_content=None, model_extra={}, tool_calls=None),
                                 finish_reason="stop")], usage=None, model_extra={})
        return gen()


async def fake(client, **kw):
    calls.append(1)
    if mode["fail"]:
        raise E("Error code: 429 - {'error': {'message': 'Rate limit reached for tokens per day (TPD)'}}",
                {"retry-after": "5400", "x-ratelimit-reset-tokens": "1h29m"})
    return Stream()
headless.chat_create = fake


def turn():
    got = []
    asyncio.run(headless.run_once("hello", model="llama-3.3-70b", history=[], chat_id="c1",
                                  permission_check=lambda *a: "allow", emit=lambda k, **f: got.append((k, f))))
    return got


got = turn()
err = [f for k, f in got if k == "error"]
check(f"the first turn asks and fails ({len(calls)} call, {err[0]['category'] if err else None})",
      len(calls) == 1 and err and err[0]["category"] == "quota")
kept = json.load(open(L.PATH))
check(f"the reset is kept per provider and key, the key itself never ({kept})",
      len(kept) == 1 and list(kept)[0].startswith("api.groq.com|") and "gsk_secret" not in json.dumps(kept))
got = turn()
err = [f for k, f in got if k == "error"]
check(f"the next turn doesn't call, and says until when ({len(calls)} calls, {err[0]['message'] if err else None!r})",
      len(calls) == 1 and err and err[0]["message"].startswith("Groq is out of usage until") and ("done", ) == got[-1][:1])
headless.LOCAL_API_KEY = "gsk_other_key"
mode["fail"] = False
turn()
check("another key for the same provider isn't held back", len(calls) == 2)
headless.LOCAL_API_KEY = "gsk_secret_key_1234567890"
d = json.load(open(L.PATH))
for k in d:
    d[k]["until"] = time.time() - 1
json.dump(d, open(L.PATH, "w"))
got = turn()
check("once the time has passed it asks again, and a call that goes through clears it",
      len(calls) == 3 and not L.blocked(headless.LOCAL_API_BASE, headless.LOCAL_API_KEY)
      and json.load(open(L.PATH)) == {})

print("\n" + ("all provider limit checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
