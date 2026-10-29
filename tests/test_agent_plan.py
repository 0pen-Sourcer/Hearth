"""An agent's "not on your plan" is said plainly, with one tap back to Auto.

The owner, on Cursor's free plan, picked a model it doesn't include and got
"Cursor gave no answer. ActionRequiredError: Model unavailable on Free Free
includes Grok 4.7, Grok 4.6, and Auto with fixed settings...". And with Auto
picked in Inglen it still failed: Auto sent no --model, so Cursor ran the model
it had saved last. Now Auto runs Cursor's "auto"; a refusal reads "Cursor's
free plan doesn't include <model>. Pick Auto, Grok 4.7 or Grok 4.6." (the names
from Cursor's own message), carries a Use Auto switch, and the model is marked
in the menu until it answers. Codex's and Claude Code's refusals the same.
Stand-in Cursor CLI.

    python tests/test_agent_plan.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
work = tempfile.mkdtemp(prefix="inglen-plan-")
os.environ.update(HOME=work, USERPROFILE=work, HEARTH_WORKSPACE=work, JARVIS_WORKSPACE=work,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
sys.path.insert(0, ROOT)

from hearth import claude_brain as cb  # noqa: E402
from hearth import cursor_brain as cur  # noqa: E402
from hearth import plan_refusals  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


plan_refusals._FILE = os.path.join(work, "plan_refused.json")
cb._HOME = work
cb._SESSIONS = os.path.join(work, "sessions.json")
cur._LIST_CACHE = os.path.join(work, "cursor_models.json")
cb.set_bridge_url("http://127.0.0.1:9")
cb.current = lambda: "cursor-agent"
argv_log = os.path.join(work, "argv.jsonl")
fake = os.path.join(work, "fake_cursor.py")
with open(fake, "w", encoding="utf-8") as f:
    f.write(f"""import json, sys
if "--list-models" in sys.argv:
    print("Available models\\n\\nauto - Auto (current, default)\\ngrok-4.7 - Grok 4.7\\n"
          "claude-opus-5-thinking-high - Claude Opus 5 1M Thinking\\n\\nTip: use --model")
    sys.exit(0)
sys.stdin.read()
open({argv_log!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "(its own saved one)"
out = lambda o: print(json.dumps(o), flush=True)
if model in ("auto", "grok-4.7"):
    out({{"type": "system", "subtype": "init", "session_id": "s1", "model": model}})
    out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Hi."}}]}}, "timestamp_ms": 1}})
    out({{"type": "result", "subtype": "success", "is_error": False, "result": "Hi.", "session_id": "s1", "usage": {{}}}})
else:
    sys.stderr.write("ActionRequiredError: Model unavailable on Free\\nFree includes Grok 4.7, Grok 4.6, and Auto "
                     "with fixed settings. Switch to an included model to continue.\\n")
    sys.exit(1)
""")
real_exe_for = cb.exe_for
cb.exe_for = lambda p: [sys.executable, fake] if p == "cursor-agent" else real_exe_for(p)
saved = {"model": ""}
cb.chosen_model = lambda p: saved["model"]
cb.chosen_effort = lambda: ""


def turn(prompt):
    events = []
    asyncio.run(cb.run_turn(prompt, emit=lambda t, **f: events.append((t, f)), history=[],
                            permission_check=lambda n, a: "allow"))
    return events


def ran_model():
    a = json.loads(open(argv_log).read().splitlines()[-1])
    return a[a.index("--model") + 1] if "--model" in a else None


ev = turn("hello")
check(f"Auto runs Cursor's own auto model, named ({ran_model()})", ran_model() == "auto")
check("and it answers", any(t == "assistant" for t, _ in ev))

saved["model"] = "claude-opus-5-thinking-high"
ev = turn("hello again")
err = next((f for t, f in ev if t == "error"), {})
check(f"a model the plan refuses is said plainly {err.get('message')!r}",
      err.get("message") == "Cursor's free plan doesn't include Claude Opus 5 1M Thinking. Pick Auto, Grok 4.7 or Grok 4.6.")
check("as a plan refusal, not a bare stop", err.get("category") == "plan" and "ActionRequiredError" not in err.get("message", ""))
check(f"with one tap back to Auto {err.get('switch')}",
      err.get("switch") == {"provider": "cursor-agent", "model": "", "label": "Use Auto"})
rows = {m["id"]: m for m in cb.agent_models("cursor-agent")["models"]}
check("the menu marks the model the plan refused", rows["claude-opus-5-thinking"].get("refused") == "Free"
      and not rows["grok-4.7"].get("refused") and not rows[""].get("refused"))

saved["model"] = ""                                       # the tap: back to Auto
ev = turn("and now?")
check("after the switch it answers on Auto", ran_model() == "auto" and any(t == "assistant" for t, _ in ev))

saved["model"] = "claude-opus-5-thinking-high"
fake_ok = open(fake).read().replace('if model in ("auto", "grok-4.7")', 'if True')
open(fake, "w").write(fake_ok)                            # the plan now includes it
turn("upgraded")
rows = {m["id"]: m for m in cb.agent_models("cursor-agent")["models"]}
check("a refused model that answers later loses its mark", not rows["claude-opus-5-thinking"].get("refused"))

# The other agents' own words.
cdx = plan_refusals.parse("Codex stopped: The 'gpt-5.2-codex' model is not supported when using Codex with a "
                          "ChatGPT account.")
check(f"Codex's refusal is read {cdx}", cdx and cdx["model"] == "gpt-5.2-codex" and cdx["plan"] == "ChatGPT")
check(f"and said plainly: {plan_refusals.say('Codex', cdx)!r}",
      plan_refusals.say("Codex", cdx) == "Codex's ChatGPT plan doesn't include gpt-5.2-codex. Pick another model.")
cc = plan_refusals.parse("Claude Pro users are not currently able to use Opus in Claude Code.")
check(f"Claude Code's refusal is read and said: {plan_refusals.say('Claude Code', cc)!r}",
      cc and plan_refusals.say("Claude Code", cc) == "Claude Code's Pro plan doesn't include Opus. Pick another model.")
check("ordinary errors are left alone", plan_refusals.parse("Cursor stopped: network error") is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent plan checks passed")
