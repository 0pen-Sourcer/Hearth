"""The reminders file, shared by the window and the terminal.

Both run a watcher over the same file. Each loaded it, fired what was due,
then saved, so both fired the same reminder, and a watcher that spent a few
seconds firing wrote back a copy from before any reminder set meanwhile,
erasing it. Saves also truncated the file in place, and a read that landed
mid-write came back empty, so the next save kept only its one new entry.

Runs real processes against a throwaway workspace.

    .\\.venv\\Scripts\\python.exe tests\\test_reminder_store.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-remstore-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["LOCAL_API_BASE"] = "http://127.0.0.1:9/v1"      # a fired reminder never reaches a real model
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


# A watcher in its own process. Each fire takes a moment, as a toast or an
# action does, and is recorded to a file of its own.
WATCHER = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from hearth import reminders as R
out, pause = sys.argv[2], float(sys.argv[3])
fired = []
def notify(title, body):
    time.sleep(pause)
    fired.append(body)
    with open(out, "w") as f:
        json.dump(fired, f)
R.start_watcher(notify)
time.sleep(float(sys.argv[4]))
'''


def watcher(tag, pause, run_for):
    out = os.path.join(WS, f"fired_{tag}.json")
    p = subprocess.Popen([sys.executable, "-X", "utf8", "-c", WATCHER, ROOT, out,
                          str(pause), str(run_for)],
                         env=dict(os.environ), cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p, out


def fired(out):
    try:
        return json.load(open(out))
    except (OSError, ValueError):
        return []


past = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")


def seed(n):
    R._save_all([{"id": f"r_{i}", "due_iso": past, "what": f"due {i}", "fired": False}
                 for i in range(n)])


# ----------------------------------------------- two watchers, one reminder each
seed(20)
a, out_a = watcher("a", 0.05, 6)
b, out_b = watcher("b", 0.05, 6)
a.wait(); b.wait()
got = fired(out_a) + fired(out_b)
dups = sorted({w for w in got if got.count(w) > 1})
check(f"two watchers fire each due reminder once, not twice ({len(got)} fires for 20, "
      f"{len(dups)} doubled)", len(got) == 20 and not dups)

# ------------------------------------------- a reminder set while one is firing
for f in (out_a, out_b):
    if os.path.exists(f):
        os.remove(f)
seed(1)
slow, out_slow = watcher("slow", 3.0, 6)
time.sleep(1.5)                       # the watcher is inside its slow fire
res = R.set_reminder("1h", "set while the other was firing")
slow.wait()
kept = [r.get("what") for r in R._load_all()]
check("a reminder set while the other process was firing is still there",
      res.get("ok") and "set while the other was firing" in kept)
check("and the one that fired is marked fired",
      any(r.get("id") == "r_0" and r.get("fired") for r in R._load_all()))

# ----------------------------------------------------------- a damaged file
with open(R.REMINDERS_PATH, "w") as f:
    f.write('[{"id": "r_keep", "due_iso": "2099-01-01T00:00:00", "what": "keep me"')
R.set_reminder("2h", "after the damage")
aside = [f for f in os.listdir(WS) if f.startswith("reminders.json.corrupt-")]
check("a damaged file is set aside, not overwritten by the next save",
      aside and "keep me" in open(os.path.join(WS, aside[0])).read())

# ------------------------------------------------ saves never show half a file
R.set_reminder("3h", "one")
check("a save leaves no staged file behind",
      not [f for f in os.listdir(WS) if f.startswith(".hearth-tmp-")])

# A reminder could be cancelled or pushed ten minutes, never held.
_past = (datetime.now() - timedelta(hours=5, minutes=1)).isoformat(timespec="seconds")
R._save_all([{"id": "r_rep", "due_iso": _past, "what": "stretch", "fired": False, "recurring_seconds": 7200},
             {"id": "r_one", "due_iso": _past, "what": "call mom", "fired": False}])
paused = [R.pause_reminder("r_rep"), R.pause_reminder("r_one")]
check("pausing a reminder says so", all(p.get("ok") and p.get("paused") for p in paused))
check("a paused reminder that comes due does not fire", R._claim_due() == [])
# A reminder couldn't be changed, only cancelled and set again.
_soon = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
ch = R.update_reminder("r_one", what="Call mom back", when=_soon, recurring="daily",
                       until=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"))
_r1 = next(r for r in R.list_reminders(include_fired=True) if r["id"] == "r_one")
check(f"a reminder's words, time, repeat and last day can be changed ({ch.get('set_for')})",
      ch.get("ok") and _r1["what"] == "Call mom back" and _r1["due_iso"].startswith(_soon)
      and _r1["recurring_seconds"] == 86400 and _r1["until_iso"].endswith("T23:59:59") and not _r1.get("fired"))
check("a time already gone is refused", not R.update_reminder("r_one", when="2001-01-01T09:00").get("ok"))
R.update_reminder("r_one", recurring="")
check("a repeat can be taken off", "recurring_seconds" not in next(r for r in R.list_reminders(True) if r["id"] == "r_one"))
_last = {"id": "x", "due_iso": (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"),
         "recurring_seconds": 86400, "until_iso": datetime.now().strftime("%Y-%m-%dT23:59:59")}
R._advance_after_fire(_last, datetime.now(), datetime.now().isoformat(timespec="seconds"))
check("a repeating one past its last day stops instead of coming back", _last.get("fired") is True)
back = R.pause_reminder("r_rep", False)
check(f"a repeating one resumes at its next time, not on the slots it skipped ({back.get('due_iso')})",
      back.get("ok") and not back.get("paused") and back["due_iso"] > datetime.now().isoformat(timespec="seconds")
      and R._claim_due() == [])

# What JARVIS is handed when a reminder fires: one missed while Hearth was
# closed says when it was due; one that wants a risky tool asks, in plain words.
_due = (datetime.now() - timedelta(hours=2)).replace(second=0, microsecond=0)
R._save_all([{"id": "r_late", "what": "call mom", "due_iso": _due.isoformat(timespec="seconds")},
             {"id": "r_act", "what": "open the report", "due_iso": _due.isoformat(timespec="seconds"),
              "action_tool": "run_command", "action_args": {"command": "start report.pdf"}}])
_notes = {}
_real_deliver = R.deliver
R.deliver = lambda r, note, **_k: _notes.setdefault(r["id"], note)
R._watcher_stop.clear()
R.start_watcher(lambda *_a: None)
_end = time.time() + 10
while time.time() < _end and len(_notes) < 2:
    time.sleep(0.1)
R.stop_watcher()
R.deliver = _real_deliver
_late = _notes.get("r_late", "")
check(f"a missed reminder says when it was due ({_late[:90]})",
      "while Hearth was closed" in _late and R._when_said(_due.isoformat()) in _late)
_act = _notes.get("r_act", "")
check(f"a reminder that wants a risky tool asks first, without shouting ({_act[:70]})",
      "has not run" in _act and "ask whether to go ahead" in _act
      and not any(w in _act for w in ("NOT", "ASK", "the user")))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder store checks passed")
