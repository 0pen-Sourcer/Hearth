"""A reminder that fires when a file or folder changes, not at a time.

Reminders only came due by the clock, so "when a PDF lands in Downloads,
summarize it" could not be set at all.

    .\\.venv\\Scripts\\python.exe tests\\test_reminder_watch.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-remwatch-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import reminders as R                                   # noqa: E402

if not os.path.abspath(R.REMINDERS_PATH).lower().startswith(os.path.abspath(WS).lower()):
    sys.exit(f"refusing to run: reminders resolved to {R.REMINDERS_PATH}")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


INBOX = os.path.join(WS, "inbox")
os.makedirs(INBOX)
once = R.set_reminder("", "summarize the new file", watch=INBOX)
every = R.set_reminder("", "note each change", recurring="yes", watch=INBOX)
check(f"a folder can be watched instead of a time {once.get('set_for') or once}",
      once.get("ok") and every.get("ok") and "inbox" in once["set_for"])
check("nothing fires while nothing changes", R._claim_due() == [] and R._claim_due() == [])

with open(os.path.join(INBOX, "report.pdf"), "wb") as f:
    f.write(b"half")
first = R._claim_due()
with open(os.path.join(INBOX, "report.pdf"), "ab") as f:
    f.write(b" done")
second = R._claim_due()
third = [r["what"] for r in R._claim_due()]
check(f"a file still being written is not reported until it holds still {first} {second} {third}",
      first == [] and second == [] and sorted(third) == ["note each change", "summarize the new file"])

with open(os.path.join(INBOX, "two.txt"), "w") as f:
    f.write("x")
R._claim_due()
again = [r["what"] for r in R._claim_due()]
check(f"a one-time watch fires once; one set to repeat fires on each change {again}",
      again == ["note each change"])

check("a watch in a folder that does not exist is refused",
      not R.set_reminder("", "x", watch=os.path.join(WS, "nope", "deeper", "f.txt")).get("ok"))
check("a file that does not exist yet can be watched for",
      R.set_reminder("", "it arrived", watch=os.path.join(INBOX, "later.zip")).get("ok"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder watch checks passed")
