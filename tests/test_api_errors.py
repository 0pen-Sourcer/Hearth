"""What Hearth says when a cloud provider turns a request away.

A free plan with a small per-minute token allowance rejects one Hearth
request outright (HTTP 413). Retrying can't help, so the message has to say
the plan is too small, with the numbers, instead of "unexpected error".

    .\\.venv\\Scripts\\python.exe tests\\test_api_errors.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth.errors import classify_api_error                         # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Err(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


too_big = Err(413, "Error code: 413 - {'error': {'message': 'Request too large for model `openai/gpt-oss-120b` "
                   "in organization `org_x` service tier `on_demand` on tokens per minute (TPM): Limit 8000, "
                   "Requested 8297, please reduce your message size and try again.', 'type': 'tokens', "
                   "'code': 'rate_limit_exceeded'}}")
info = classify_api_error(too_big, is_local=False)
check("a request bigger than the plan's per-minute allowance is named as such", info.category == "too_big_for_plan")
check("with the numbers", "8,000 tokens a minute" in info.hint and "is 8,297." in info.hint)
check("and isn't retried, since the next try is just as big", info.retryable is False)

limited = Err(429, "Error code: 429 - {'error': {'code': 'rate_limit_exceeded', 'message': 'Rate limit reached, try again in 2s'}}")
info = classify_api_error(limited, is_local=False)
check("an ordinary rate limit is still a rate limit, retried", info.category == "rate_limit" and info.retryable)

plain = Err(400, "something odd")
check("anything else is not mistaken for a plan limit",
      classify_api_error(plain, is_local=False).category != "too_big_for_plan")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all api error checks passed")
