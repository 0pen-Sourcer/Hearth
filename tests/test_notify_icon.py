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

# With Hearth's tray icon in this process, a notice goes through it and adds no
# icon of its own (every added one that isn't taken down is a ghost in the tray).
through_tray = []
reminders.use_tray(lambda t, b: through_tray.append((t, b)))
shown.clear()
reminders.desktop_notify("Hearth", "Stretch")
reminders.use_tray(None)
check("in the window's process a notice goes through the tray icon",
      through_tray == [("Hearth", "Stretch")] and not shown)

# Without one (the terminal), the notice's own icon comes down even when the
# process ends before the notice does.
if sys.platform == "win32":
    import threading as _th
    _bt = types.ModuleType("plyer.platforms.win.libs.balloontip")
    _bt.WindowsBalloonTip = None
    sys.modules["plyer.platforms.win.libs.balloontip"] = _bt
    opened, removed, gate = [], [], _th.Event()

    class _Tip:
        def __init__(self, **kw):
            opened.append(kw.get("app_icon"))
            gate.wait(5)              # the notice is still up
        def remove_notify(self):
            removed.append(1)

    real_tip = _bt.WindowsBalloonTip
    _bt.WindowsBalloonTip = _Tip
    try:
        reminders.desktop_notify("Hearth", "Water")
        end = __import__("time").time() + 3
        while not opened and __import__("time").time() < end:
            __import__("time").sleep(0.02)
        reminders._take_down_all()        # what runs at exit
        gate.set()
    finally:
        _bt.WindowsBalloonTip = real_tip
    check(f"the terminal's notice carries the icon and is taken down at exit ({opened}, {len(removed)})",
          opened and opened[0] == icon and len(removed) >= 1)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all notify icon checks passed")
