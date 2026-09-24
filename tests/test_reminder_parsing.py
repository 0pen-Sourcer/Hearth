"""Working out when a reminder is for.

The model turns what was said into a time. It is given the exact local time on
every turn and it understands language; a pattern list does neither. The old
parser guessed at English, and a guess is worse than a refusal: it read
"tonight at 11" as eleven in the morning and said nothing about it.

So `when` takes only forms that mean one thing, a local ISO timestamp or a
duration from now, and anything else is refused with the current time and the
shape to send, so the model converts it and tries again.

Time is frozen in here rather than read off the clock.

    .\\.venv\\Scripts\\python.exe tests\\test_reminder_parsing.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import reminders as R                                   # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


NOW = datetime(2026, 9, 24, 6, 0)          # a Thursday, six in the morning

# ------------------------------------------------------------ what is read
READ = [
    ("2026-09-24T23:00", datetime(2026, 9, 24, 23, 0)),
    ("2026-09-24T23:00:00", datetime(2026, 9, 24, 23, 0)),
    ("2026-09-24 23:00", datetime(2026, 9, 24, 23, 0)),
    ("2026-12-25T09:30", datetime(2026, 12, 25, 9, 30)),
    ("25m", NOW + timedelta(minutes=25)),
    ("2h", NOW + timedelta(hours=2)),
    ("3d", NOW + timedelta(days=3)),
    ("90s", NOW + timedelta(seconds=90)),
    ("1.5h", NOW + timedelta(hours=1.5)),
    ("in 25 minutes", NOW + timedelta(minutes=25)),
    ("in 2 hours", NOW + timedelta(hours=2)),
    ("1 week", NOW + timedelta(weeks=1)),
]
for text, want in READ:
    got = R.parse_when(text, NOW)
    check(f"{text!r} is {want:%a %d %b %H:%M}" + ("" if got == want else f", got {got}"),
          got == want)

# A timestamp carrying an offset means that instant, shown in local time.
utc = datetime(2026, 9, 24, 17, 30, tzinfo=timezone.utc)
check("an offset is honoured and turned into local time",
      R.parse_when("2026-09-24T17:30:00Z", NOW) == utc.astimezone().replace(tzinfo=None))

# ------------------------------------------------ what is left to the model
# Every one of these is something a person says. None of them is guessed at
# here: the model has the time and does the converting.
for text in ("tonight at 11", "tomorrow at 7am", "at 3pm", "friday 6pm",
             "next monday", "in an hour", "noon", "in a bit", "soon", ""):
    check(f"{text!r} is left to the model rather than guessed",
          R.parse_when(text, NOW) is None)

# A time that cannot exist is not folded into one that can.
for text in ("2026-09-24T25:00", "2026-13-01T09:00", "2026-02-30T09:00"):
    check(f"{text!r} is refused", R.parse_when(text, NOW) is None)

# ------------------------------------------------- what the model is told
R.REMINDERS_PATH = os.path.join(tempfile.mkdtemp(), "reminders.json")

r = R.set_reminder("tonight at 11", "take the bread out")
err = r.get("error") or ""
today = datetime.now()
check("a phrase is refused, not set", r.get("ok") is False)
check("and the refusal carries the current local time to convert from",
      f"{today:%Y-%m-%d}" in err and f"{today:%A}" in err)
check("and the shape to send", "ISO" in err and "25m" in err)

past = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
r = R.set_reminder(past, "this was a slip")
check("a time that has already passed is refused rather than fired at once",
      r.get("ok") is False and "already passed" in (r.get("error") or ""))

soon = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
r = R.set_reminder(soon, "call mum")
check("a real timestamp is set", r.get("ok") is True)
check("and lands at that time",
      (r.get("reminder") or {}).get("due_iso", "").startswith(soon))
r = R.set_reminder("25m", "check the oven")
check("a duration is set", r.get("ok") is True)

# ------------------------------------------------ the model is told the same
from hearth import tools as T                                       # noqa: E402
from hearth.persona import system_prompt                            # noqa: E402

desc = next(t for t in T.TOOL_DEFINITIONS if t["name"] == "set_reminder")
text = desc["description"] + desc["parameters"]["properties"]["when"]["description"]
check("the tool no longer invites natural language",
      "Natural-language OK" not in text and "tomorrow at 7am" not in text)
check("it asks for a timestamp or a duration", "ISO" in text and "25m" in text)
check("and the persona agrees with it",
      "tomorrow at 7am" not in system_prompt())

# ------------------------------------------ recurrence is a separate field
for spec, secs in (("every 30 minutes", 1800), ("daily", 86400),
                   ("hourly", 3600), ("weekly", 604800), ("30m", 1800)):
    check(f"{spec!r} repeats every {secs}s", R._parse_recurring(spec) == secs)
check("a one-shot has no repeat", R._parse_recurring("") is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder checks passed")
