"""A repeating reminder doesn't talk to an empty room.

A 2-hourly "stretch" fired all night, each one a full reply in its chat that
riffed on the one before. Now one that comes due while nobody has touched the
PC for its own interval is held and counted, the next one with them back says
how many came by, and each sees only its last exchange.

    .\\.venv\\Scripts\\python.exe tests\\test_reminder_away.py
"""
from __future__ import annotations

import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-remaway-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import reminders  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


stretch = {"id": "r1", "what": "stand up and stretch", "recurring_seconds": 7200}
reminders.idle_seconds = lambda: 3 * 3600.0          # away three hours
check("due while they've been away longer than its interval, it's held",
      reminders.held_while_away(stretch) and reminders.held_while_away(stretch) and reminders._AWAY.get("r1") == 2)
reminders.idle_seconds = lambda: 30.0                 # back at the keyboard
check("with them back it's said", not reminders.held_while_away(stretch))
once = {"id": "r2", "what": "call mum", "recurring_seconds": None}
reminders.idle_seconds = lambda: 3 * 3600.0
check("a one-off is never held, away or not", not reminders.held_while_away(once))
action = {"id": "r3", "what": "back up", "recurring_seconds": 3600, "action_tool": "run_command"}
check("one that runs something still runs", not reminders.held_while_away(action))
reminders.idle_seconds = lambda: None
check("where idle time can't be read, nothing is held", not reminders.held_while_away({"id": "r4", "recurring_seconds": 60}))

src = open(os.path.join(ROOT, "hearth", "reminders.py"), encoding="utf-8").read()
check("the next one says how many came by", "came due {away} time" in src and "_AWAY.pop(" in src)
check("each sees only its last exchange, not twenty", "history=prior[-2:]" in src and "prior[-20:]" not in src)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder away checks passed")
