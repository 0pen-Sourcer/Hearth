"""Out of usage is said once, plainly, and nothing is spent finding out again.

Claude Code, out of usage, writes the limit line as text as well as ending
the turn with an error; Hearth posted that line as JARVIS's reply, and a
reminder that came due became "You've hit your session limit" in its chat.
Each later turn started the agent again just to hear the same refusal.

    .\\.venv\\Scripts\\python.exe tests\\test_out_of_usage.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="hearth-usage-")
WS = tempfile.mkdtemp(prefix="hearth-usage-ws-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
sys.path.insert(0, ROOT)

from hearth import claude_brain as cb  # noqa: E402

cb._HOME = HOME
cb._LIMITS = os.path.join(HOME, "plan_limits.json")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A refusal is kept as used up until it resets; past 100% alone is not.
soon = int(time.time()) + 3600
cb.note_claude_limits({"rate_limit_info": {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 1.0, "resetsAt": soon}}}})
check("at 100% but still allowed (extra usage), the agent is still started", cb.used_up("claude-code") is None)
cb.note_claude_limits({"rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": soon}})
full = cb.used_up("claude-code")
check(f"a refusal is remembered until it resets ({full})", full and full["window"] == "5 hours")

# The next turn isn't started; the error says until when.
said = []
cb.current = lambda: "claude-code"
started = []
cb._run_claude = lambda *a, **k: started.append(1)
asyncio.run(cb.run_turn("hello", emit=lambda kind, **kw: said.append((kind, kw))))
errs = [kw for kind, kw in said if kind == "error"]
check(f"the agent isn't started again just to be refused ({errs and errs[0]['message']})",
      not started and errs and errs[0].get("category") == "quota" and "until" in errs[0]["message"])

# Past the reset it runs again.
cb.note_claude_limits({"rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": int(time.time()) - 5}})
check("once the window has reset the agent runs again", cb.used_up("claude-code") is None)

# A reminder whose turn ends in that error says the reminder and why.
from hearth import reminders, headless  # noqa: E402
posted = []


async def refused(note, emit, **kw):
    emit("error", message="Claude Code is out of usage on your Claude plan until 3:20 PM")
    emit("done")

headless.run_once = refused
headless.turn_is_active = lambda: False
from hearth import chat_sync  # noqa: E402
chat_sync.post = lambda cid, title, src, msgs, unread=False: posted.append(msgs[0]["content"])
reminders.deliver({"id": "r1", "what": "Stand up and stretch"}, "note", wait_s=0).join(10)
check(f"a reminder that couldn't be phrased says itself and why ({posted})",
      posted and posted[0].startswith("Reminder: Stand up and stretch") and "out of usage" in posted[0]
      and "session limit" not in posted[0])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all out-of-usage checks passed")
