"""A step held back in a voice turn tells the brain why it asks.

On a call in Auto, a command that opened Chrome on an address nobody gave (after
the chat had looked at the screen) came back to the brain only as "this step can
only be allowed with a tap", so it had nothing true to tell the person and made
something up. The reason Auto stopped it now rides with the step. Temporary
workspace; nothing runs.

    .\\.venv\\Scripts\\python.exe tests\\test_voice_ask_why.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
with open(os.path.join(_home.WORKSPACE, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"approval_mode": "auto"}, f)

from hearth import auto_review, call as C                             # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


history = [{"role": "user", "content": "open chrome"},
           {"role": "assistant", "content": "Chrome is open.",
            "events": [{"kind": "tool", "name": "desktop_snapshot", "args": {"window": "Google Chrome"},
                        "result": "Window: Google Chrome\n  [0] Document: Who's using Chrome?"}]}]
prompt = "open the second profile and check if my x account is connected"
auto_review.begin_turn("c_call", prompt, history, None)
check_fn = C.voice_permission_check(lambda n, a: "deny", lambda *a, **k: None, prompt, history,
                                    asks={}, tapped=set(), says_why=lambda: True)

out = check_fn("run_command", {"command": 'Start-Process chrome -ArgumentList "--profile-directory=Profile 2",'
                                          '"https://www.google.com"'})
reason = out.get("reason", "") if isinstance(out, dict) else ""
check(f"a held step goes back to the brain to ask about ({out if not isinstance(out, dict) else out['decision']})",
      isinstance(out, dict) and out.get("decision") == "ask_aloud")
check(f"with why it asks: an address nobody gave after reading the screen ({reason[-200:]})",
      "Why it asks" in reason and "www.google.com" in reason and "the screen" in reason)

out = check_fn("run_command", {"command": "Get-Process -Id 9084 | Stop-Process -Force"})
reason = out.get("reason", "") if isinstance(out, dict) else ""
check(f"a destructive command says what in it asks ({reason[-120:]})", "Why it asks" in reason and "Stop-Process" in reason)

out = check_fn("run_command", {"command": "Get-ChildItem C:\\Users"})
check(f"an ordinary command still runs in Auto ({out})", out == "allow")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all voice ask-why checks passed")
