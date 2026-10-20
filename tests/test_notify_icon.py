"""Reminder notices carry Hearth's icon from every surface.

The window's reminder watcher had its own copy of the notifier that called
plyer without an icon, so its notices showed the generic Windows one while
the terminal's showed Hearth's.

    .\\.venv\\Scripts\\python.exe tests\\test_notify_icon.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-notifyicon-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ.pop("HEARTH_NTFY_TOPIC", None)
sys.path.insert(0, ROOT)

from hearth import reminders  # noqa: E402
from hearth import web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# plyer stand-in that records what it was asked to show; no win10toast.
shown: list = []
plyer = types.ModuleType("plyer")
plyer.notification = types.SimpleNamespace(notify=lambda **kw: shown.append(kw))
sys.modules["plyer"] = plyer
sys.modules["win10toast"] = None

check("Hearth's .ico is found", bool(reminders._find_icon()))

handed = {}
real_start = reminders.start_watcher
reminders.start_watcher = lambda fn: handed.setdefault("fn", fn)
try:
    web._start_reminder_watcher()
finally:
    reminders.start_watcher = real_start
check("the window starts a watcher", "fn" in handed)
if "fn" in handed:
    handed["fn"]("Hearth", "Stretch")
check("the window's notice reached the notifier", len(shown) == 1)
icon = shown[0].get("app_icon") if shown else None
check(f"and carries Hearth's icon ({icon})",
      bool(icon) and str(icon).lower().endswith(".ico") and os.path.isfile(icon))

shown.clear()
reminders.desktop_notify("Hearth", "Water")
check("the shared notifier passes the icon too",
      bool(shown) and shown[0].get("app_icon") == icon)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all notify icon checks passed")
