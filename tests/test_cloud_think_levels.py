"""A cloud model's thinking control has that model's own levels.

The levels come from the provider's model list where it names them
(OpenRouter's per-model supported_efforts, whether reasoning is mandatory)
and from the model catalog's reasoning_options otherwise. Thinking off sends
the value the model lists for off, or nothing; a model that refuses one level
still takes the others.

    .\\.venv\\Scripts\\python.exe tests\\test_cloud_think_levels.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import types

work = tempfile.mkdtemp(prefix="hearth-cloud-think-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import chat_call, headless, model_facts  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# The catalog, as models.dev gives it, without the network.
model_facts._INDEX.clear()
model_facts._INDEX.update({
    "openai": {"gpt-new": {"reasoning": True, "reasoning_options": [{"type": "effort", "values": ["none", "low", "medium", "high"]}]},
               "gpt-nano": {"reasoning": True, "reasoning_options": [{"type": "effort", "values": ["minimal", "low", "medium", "high"]}]},
               "gpt-plain": {"reasoning": False}},
    "deepseek": {"ds-think": {"reasoning": True, "reasoning_options": [{"type": "toggle"},
                                                                       {"type": "effort", "values": ["low", "high", "max"]}]}},
    "google": {"gem-pro": {"reasoning": True, "reasoning_options": [{"type": "budget_tokens", "min": 128, "max": 32768}]}},
})
model_facts._LOADED[0] = time.time()
model_facts._load = lambda fetch=True: {}

check("the catalog's effort levels and off value are read",
      model_facts.from_catalog("openai", "gpt-new") == {"reasoning": True, "efforts": ["low", "medium", "high"],
                                                         "can_off": True, "off": "none"})
nano = model_facts.from_catalog("openai", "gpt-nano")
check(f"levels without none can't be switched off {nano}",
      nano.get("efforts") == ["minimal", "low", "medium", "high"] and nano.get("can_off") is False)
ds = model_facts.from_catalog("deepseek", "ds-think")
check(f"a switch beside levels without none isn't an off this field reaches {ds}",
      ds.get("can_off") is False and ds.get("efforts") == ["low", "high", "max"])
check("a switch alone means it can be switched off",
      model_facts.reasoning_levels([{"type": "toggle"}]) == {"can_off": True})
check("a budget alone gives no levels", "efforts" not in model_facts.from_catalog("gemini", "gem-pro"))

OPENAI, OR = headless.PROVIDERS["openai"][1], headless.PROVIDERS["openrouter"][1]
check("thinking off sends what the model lists for off, not minimal",
      headless._cloud_reasoning_effort(OPENAI, False, "gpt-new") == "none")
check("a model with no off value is sent nothing to switch off",
      headless._cloud_reasoning_effort(OPENAI, False, "gpt-nano") is None)
check("a model that doesn't reason is sent nothing",
      headless._cloud_reasoning_effort(OPENAI, False, "gpt-plain") is None)
check("a level the model lists is sent", headless._cloud_reasoning_effort(OPENAI, True, "gpt-nano", "minimal") == "minimal")
check("a level it doesn't list is not", headless._cloud_reasoning_effort(OPENAI, True, "gpt-new", "xhigh") is None)

# OpenRouter's list names each model's levels.
rows = {"data": [
    {"id": "anthropic/claude-x", "supported_parameters": ["reasoning", "reasoning_effort"],
     "reasoning": {"supported_efforts": ["max", "xhigh", "high", "medium", "low"], "default_effort": "high",
                   "mandatory": True}},
    {"id": "deepseek/ds-pro", "supported_parameters": ["reasoning"],
     "reasoning": {"supported_efforts": ["xhigh", "high", "none"], "mandatory": False}},
]}
real_open = headless.__dict__.get("_ur")
import urllib.request  # noqa: E402
_orig = urllib.request.urlopen


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


urllib.request.urlopen = lambda req, timeout=None: _Resp(json.dumps(rows).encode())
headless.provider_models(OR, "k")
urllib.request.urlopen = _orig
cx = headless.listed_facts(OR, "anthropic/claude-x")
check(f"OpenRouter's levels per model, in order, and mandatory means no off {cx}",
      cx.get("efforts") == ["low", "medium", "high", "xhigh", "max"] and cx.get("can_off") is False)
dp = headless.listed_facts(OR, "deepseek/ds-pro")
check(f"one that lists none can be switched off with it {dp}", dp.get("efforts") == ["high", "xhigh"]
      and dp.get("can_off") is True and headless._cloud_reasoning_effort(OR, False, "deepseek/ds-pro") == "none")
check("thinking off on a model that must reason sends nothing",
      headless._cloud_reasoning_effort(OR, False, "anthropic/claude-x") is None)


# A provider that refuses one reasoning level keeps taking the others.
class Refused(Exception):
    def __init__(self, msg):
        super().__init__(f"Error code: 400 - {{'error': {{'message': \"{msg}\"}}}}")
        self.status_code = 400


sent = []


def create(**kw):
    sent.append(dict(kw))
    if kw.get("reasoning_effort") == "minimal":
        raise Refused("Unsupported value: 'reasoning_effort' does not support 'minimal' with this model. "
                      "Supported values are: 'none', 'low', 'medium', and 'high'.")
    return "reply"


client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
got = chat_call.chat_create(client, model="gpt-z", messages=[], reasoning_effort="minimal")
check("a refused level is dropped and the call made again",
      got == "reply" and len(sent) == 2 and "reasoning_effort" not in sent[1])
sent.clear()
chat_call.chat_create(client, model="gpt-z", messages=[], reasoning_effort="high")
check(f"another level still reaches that model {sent}", sent and sent[0].get("reasoning_effort") == "high")
sent.clear()
chat_call.chat_create(client, model="gpt-z", messages=[], reasoning_effort="minimal")
check("the refused one isn't sent again", len(sent) == 1 and "reasoning_effort" not in sent[0])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all cloud thinking level checks passed")
