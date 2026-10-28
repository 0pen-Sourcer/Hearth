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

# A model the account's own settings rule out came back as a 404 wall of JSON,
# read as "model id not found".
policy = Err(404, "Error code: 404 - {'error': {'message': '0 endpoints out of 1 requested are available matching "
                  "your guardrail restrictions and data policy. We removed them for the following reasons: Free "
                  "model training violation (account settings): 1 endpoint excluded; configurable at "
                  "https://openrouter.ai/settings/privacy', 'code': 404}}")
info = classify_api_error(policy, is_local=False)
check(f"a model ruled out by the account's own settings says so, and where to change it ({info.hint})",
      info.category == "account_policy" and "openrouter.ai/settings/privacy" in info.hint and not info.retryable)

plain = Err(400, "something odd")
check("anything else is not mistaken for a plan limit",
      classify_api_error(plain, is_local=False).category != "too_big_for_plan")

# An account with no credits got "Unexpected error talking to the cloud endpoint".
broke = Err(402, "Error code: 402 - {'error': {'message': 'Insufficient credits. This account never purchased "
                 "credits. Make sure your key is on the correct account or org, and if so, purchase more at "
                 "https://openrouter.ai/settings/credits', 'code': 402}}")
info = classify_api_error(broke, is_local=False)
check(f"no credits says so, with the provider's own words ({info.hint})",
      info.category == "no_credits" and "openrouter.ai/settings/credits" in info.hint and not info.retryable)
odd = Err(418, "Error code: 418 - {'error': {'message': 'The teapot is busy.', 'code': 418}}")
info = classify_api_error(odd, is_local=False)
check(f"an error Hearth has no name for still says what the provider said ({info.hint})", "The teapot is busy." in info.hint)


# Limits: a per-minute one clears by waiting as long as the provider asks; a
# daily quota doesn't, so it is said once instead of retried for nothing.
class _Resp:
    def __init__(self, headers):
        self.headers = headers


class _Limited(Exception):
    def __init__(self, msg, headers=None, body=None):
        super().__init__(msg)
        self.status_code = 429
        self.response = _Resp(headers or {})
        self.body = body


minute = classify_api_error(_Limited("Error code: 429 - rate limit reached for requests",
                                     headers={"retry-after": "20"}), is_local=False)
check(f"a per-minute limit is retried after the wait the provider asked for ({minute})",
      minute.category == "rate_limit" and minute.retryable and minute.wait_s == 20.0)
gem = classify_api_error(_Limited("429 RESOURCE_EXHAUSTED ... 'retryDelay': '36s'"), is_local=False)
check(f"Gemini's retryDelay is read too ({gem.wait_s})", gem.retryable and gem.wait_s == 36.0)
day = classify_api_error(_Limited(
    "429 RESOURCE_EXHAUSTED quota exceeded for metric generate_content_free_tier_requests, limit 250 per day",
    body={"error": {"message": "You exceeded your current quota, please check your plan."}}), is_local=False)
check(f"a daily quota isn't retried, and says so in the provider's words ({day.hint})",
      day.category == "quota" and not day.retryable and "You exceeded your current quota" in day.hint)
long_wait = classify_api_error(_Limited("rate limit", headers={"retry-after": "3600"}), is_local=False)
check(f"a wait of an hour is a quota too, with when ({long_wait.hint})",
      long_wait.category == "quota" and not long_wait.retryable and "60 minutes" in long_wait.hint)

from hearth import claude_brain  # noqa: E402
cc = claude_brain.out_of_usage("claude-code", {"message": "Claude Code stopped: You've hit your session limit · resets 3:20pm"})
check(f"an agent out of usage says so in its own words, with when ({cc})",
      cc.get("category") == "quota" and cc["message"].startswith("Claude Code is out of usage: ") and "Pick another" not in cc["message"]
      and "resets 3:20pm" in cc["message"] and "stopped:" not in cc["message"])
other = claude_brain.out_of_usage("codex", {"message": "Codex stopped: network error"})
check("any other stop is left as it was", other == {"message": "Codex stopped: network error"})

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all api error checks passed")
