"""Working out when a reminder is for.

People say times in a handful of ordinary ways and most of them parsed as
nothing, so the reminder was refused for phrasing that reads perfectly well:
"at 3pm", "friday 6pm", "at noon", "in an hour". Worse than any of those,
"tonight at 11" was accepted and set for eleven in the morning, twelve hours
from what was asked for, without saying anything.

Time is frozen in here rather than read off the clock, or the test would say
something different every hour.

    .\\.venv\\Scripts\\python.exe tests\\test_reminder_parsing.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import reminders as R                                   # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A Thursday, six in the morning.
NOW = datetime(2026, 9, 24, 6, 0)


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi)


CASES = [
    # the one that was wrong rather than missing
    ("tonight at 11", at(2026, 9, 24, 23)),
    ("tonight at 9", at(2026, 9, 24, 21)),
    ("tonight at 9pm", at(2026, 9, 24, 21)),
    ("tonight", at(2026, 9, 24, 20)),
    # a bare time, with and without the word people put in front of it
    ("at 3pm", at(2026, 9, 24, 15)),
    ("3pm", at(2026, 9, 24, 15)),
    ("15:30", at(2026, 9, 24, 15, 30)),
    ("at 7 a.m.", at(2026, 9, 24, 7)),
    ("at 5am", at(2026, 9, 25, 5)),             # already past, so tomorrow
    ("at noon", at(2026, 9, 24, 12)),
    ("at midnight", at(2026, 9, 25, 0)),
    # a weekday, with and without "at"
    ("friday 6pm", at(2026, 9, 25, 18)),
    ("friday at 6pm", at(2026, 9, 25, 18)),
    ("on saturday", at(2026, 9, 26, 9)),
    ("next monday at 10", at(2026, 9, 28, 10)),
    # said in words rather than digits
    ("in an hour", at(2026, 9, 24, 7)),
    ("in half an hour", at(2026, 9, 24, 6, 30)),
    ("in a couple of hours", at(2026, 9, 24, 8)),
    ("in a couple hours", at(2026, 9, 24, 8)),
    ("in a minute", at(2026, 9, 24, 6, 1)),
    ("in ten minutes", at(2026, 9, 24, 6, 10)),
    ("in two days", at(2026, 9, 26, 6)),
    # the ones that already worked, which must keep working
    ("in 5 minutes", at(2026, 9, 24, 6, 5)),
    ("in 2 hours", at(2026, 9, 24, 8)),
    ("in 1 week", at(2026, 10, 1, 6)),
    ("tomorrow at 9am", at(2026, 9, 25, 9)),
    ("tomorrow morning", at(2026, 9, 25, 9)),
    ("today at 4pm", at(2026, 9, 24, 16)),
    ("2026-12-25 09:00", at(2026, 12, 25, 9)),
    # parts of the day
    ("this evening", at(2026, 9, 24, 19)),
    ("this evening at 8", at(2026, 9, 24, 20)),
    ("this morning at 7", at(2026, 9, 24, 7)),
]

for text, want in CASES:
    got = R.parse_when(text, NOW)
    if got is not None:
        got = got.replace(second=0, microsecond=0)
    check(f"{text!r} is {want:%a %d %b %H:%M}"
          + ("" if got == want else f", got {got}"), got == want)

# Genuinely ambiguous. Refusing is the right answer; inventing a time is not.
for text in ("in a bit", "sometime later", "soon", "whenever", ""):
    check(f"{text!r} is refused rather than guessed",
          R.parse_when(text, NOW) is None)

# A time that cannot exist must not land on some other time.
for text in ("at 25:00", "at 99pm", "13:70"):
    got = R.parse_when(text, NOW)
    check(f"{text!r} does not become a real time: {got}", got is None)

# Recurrence is a separate field and still reads the same.
for spec, secs in (("every 30 minutes", 1800), ("daily", 86400),
                   ("hourly", 3600), ("weekly", 604800), ("30m", 1800)):
    check(f"{spec!r} repeats every {secs}s", R._parse_recurring(spec) == secs)
check("a one-shot has no repeat", R._parse_recurring("") is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder parsing checks passed")
