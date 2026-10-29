"""A due reminder's notice has Snooze 10 min and Done, and they act on it.

Before, the notice was a plain balloon (win10toast, plyer): snoozing or
finishing a reminder meant opening the window. Stand-ins: a notify-send that
presses a button, and a windows_toasts module that records the toast.

    python tests/test_reminder_buttons.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-rembtn-home-")
WS = os.path.join(HOME, "Inglen")
BIN = os.path.join(HOME, "bin")
os.makedirs(WS)
os.makedirs(BIN)
LOG = os.path.join(HOME, "notify.log")
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1",
                  PATH=BIN + os.pathsep + os.environ.get("PATH", ""))
sys.path.insert(0, ROOT)

# notify-send as libnotify 0.7.9+ has it: it shows the notice and prints the button pressed.
with open(os.path.join(BIN, "notify-send"), "w") as f:
    f.write(f"""#!{sys.executable}
import json, sys
if "--help" in sys.argv:
    print("  -A, --action=[NAME=]Text...  Specifies the actions to display to the user.")
    sys.exit(0)
open({LOG!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")
body = sys.argv[-1]
print("snooze" if "snz" in body else "done" if "dn" in body else "")
""")
os.chmod(os.path.join(BIN, "notify-send"), stat.S_IRWXU)

from hearth import reminders as rem  # noqa: E402
from hearth import chat_sync  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def due_now(what, recurring=""):
    r = rem.set_reminder(when="5m", what=what, recurring=recurring)["reminder"]
    with rem._store_lock():
        items = rem._load_all()
        for x in items:
            if x["id"] == r["id"]:
                x["due_iso"] = (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
        rem._save_all(items)
    return r["id"]


def get(rid):
    return next((x for x in rem._load_all() if x["id"] == rid), None)


def wait_for(cond, s=10.0):
    end = time.time() + s
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


rem.deliver = lambda r, note, wait_s=600: None          # no model here
plain = []
snz = due_now("water the plants snz")
dn = due_now("call the bank dn")
daily = due_now("stretch snz daily", recurring="daily")
daily_due = get(daily)["due_iso"]
chat_sync.post(rem.chat_id_for({"id": dn}), "call the bank", "reminder",
               [{"role": "assistant", "content": "Reminder: call the bank"}], unread=True)

rem.start_watcher(lambda t, b: plain.append((t, b)))
wait_for(lambda: os.path.exists(LOG) and len(open(LOG).read().splitlines()) >= 3)
calls = [json.loads(x) for x in open(LOG).read().splitlines()] if os.path.exists(LOG) else []
check(f"each due reminder is a notice with two buttons ({len(calls)})",
      len(calls) == 3 and all("--action=snooze=Snooze 10 min" in c and "--action=done=Done" in c for c in calls))
check("the notice says what the reminder is, in one line", any(c[-1] == "water the plants snz" for c in calls))
check("no plain notice as well", plain == [])

wait_for(lambda: not (get(snz) or {}).get("fired"))
r = get(snz)
left = (datetime.fromisoformat(r["due_iso"]) - datetime.now()).total_seconds() if r else 0
check(f"Snooze brings it back in 10 minutes ({left:.0f} s)", r and not r.get("fired") and 540 < left <= 600)

wait_for(lambda: (get(dn) or {}).get("done_at"))
check("Done keeps it as done", bool((get(dn) or {}).get("done_at")) and get(dn).get("fired"))
check("and marks its chat read", not (chat_sync.load(rem.chat_id_for({"id": dn})) or {}).get("unread"))

wait_for(lambda: len(rem._load_all()) >= 4)
d = get(daily)
extra = [x for x in rem._load_all() if x["id"] not in (snz, dn, daily)]
check("a repeating one keeps its own time after Snooze",
      d and d["due_iso"][11:16] == daily_due[11:16] and d["due_iso"] > daily_due)
check("and the snooze is a one-off in 10 minutes",
      len(extra) == 1 and extra[0]["what"] == "stretch snz daily" and not extra[0].get("recurring_seconds"))

# Windows: a toast in the reminder scenario, answered in this process.
shown = []


class _Toast:
    def __init__(self, text_fields, scenario=None):
        self.text_fields, self.scenario, self.actions, self.on_activated = text_fields, scenario, [], None

    def AddAction(self, b):
        self.actions.append(b)

    def AddImage(self, img):
        pass


fake = types.ModuleType("windows_toasts")
fake.ToastScenario = types.SimpleNamespace(Reminder="reminder")
fake.Toast = _Toast
fake.ToastButton = lambda content, arguments: (content, arguments)


class _Toaster:
    def __init__(self, text, notifierAUMID=None):
        self.aumid = notifierAUMID

    def show_toast(self, t):
        shown.append((self, t))


fake.InteractableWindowsToaster = _Toaster
sys.modules["windows_toasts"] = fake
win = rem.set_reminder(when="5m", what="pay rent")["reminder"]["id"]
ok = rem._windows_actions(win, "Inglen reminder", "pay rent", None)
toaster, toast = shown[-1] if shown else (None, None)
check("Windows shows a reminder toast with Snooze and Done, as Inglen",
      ok and toast.scenario == "reminder" and toast.actions == [("Snooze 10 min", "snooze"), ("Done", "done")]
      and toaster.aumid == rem._APP_ID and toast.text_fields == ["Inglen reminder", "pay rent"])
toast.on_activated(types.SimpleNamespace(arguments="done"))
check("its Done button acts on the reminder", bool((get(win) or {}).get("done_at")))
toast.on_activated(types.SimpleNamespace(arguments="snooze"))
check("and its Snooze button", (get(win) or {}).get("snooze_count") == 1)

# A notify-send with no buttons: the plain notice, as before.
with open(os.path.join(BIN, "notify-send"), "w") as f:
    f.write(f"#!{sys.executable}\nimport sys\nprint('Usage: notify-send [OPTION...] SUMMARY')\n")
check("without buttons on this desktop, the caller shows the plain notice",
      rem.notify_reminder("x", "Inglen reminder", "x") is False if sys.platform.startswith("linux") else True)

rem.stop_watcher()
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all reminder button checks passed")
