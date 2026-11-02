"""The 12- or 24-hour clock: one setting (clock_style), read by the window, the phone and every
time Inglen writes out. Automatic follows Windows' own format, and 12-hour where it can't be read.
The phone can change it, and a change reaches the phones at once.

    .\\.venv\\Scripts\\python.exe tests\\test_clock_style.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

ROOT = os.environ.get("HEARTH_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WS = tempfile.mkdtemp(prefix="hearth-clock-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HOME"] = os.environ["USERPROFILE"] = WS

from hearth import clock, provider_limits, relay_bridge as RB, reminders, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def use(style):
    s = web._load_settings()
    s["clock_style"] = style
    with open(web.SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f)


at = time.mktime((2026, 11, 2, 14, 34, 0, 0, 0, -1))
check(f"12-hour reads 2:34 PM ({clock.hm(at, {'clock_style': '12'})})", clock.hm(at, {"clock_style": "12"}) == "2:34 PM")
check(f"24-hour reads 14:34 ({clock.hm(at, {'clock_style': '24'})})", clock.hm(at, {"clock_style": "24"}) == "14:34")
real = clock.system_12h
clock.system_12h = lambda: None
check("automatic with no system format to read is 12-hour", clock.twelve_hour({}) is True)
clock.system_12h = lambda: False
check("automatic follows a 24-hour system clock", clock.twelve_hour({"clock_style": "auto"}) is False)
clock.system_12h = real
check("an unknown value is automatic", clock.style({"clock_style": "13"}) == "auto")

# Every time Inglen writes out follows it: a reminder's time, a provider's limit.
due = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
use("24")
r24 = reminders._when_said(due.isoformat())
lim24 = provider_limits.card("Groq", {"until": due.timestamp(), "what": "quota"})
use("12")
r12 = reminders._when_said(due.isoformat())
check(f"a reminder's time follows the clock ({r24} / {r12})",
      r24 == due.strftime("%H:%M") and r12 == due.strftime("%I:%M %p").lstrip("0"))
check(f"so does a provider's limit ({lim24})", due.strftime("%H:%M") in lim24 and "M" not in lim24.split("until ")[1][:6])

# The phone reads it with the look, may change it, and hears of a change made on the PC.
use("24")
look = RB.answer({"what": "look"}, "relay:x")
check(f"the phone's look carries the clock ({look})", look.get("clock") == "24")
check("the phone may set it to 12, 24 or automatic, nothing else",
      RB._SETTABLE.get("clock_style") == ("auto", "12", "24"))
check("a change of it on the PC reaches the phones", bool(RB.Bridge._PHONE_SETTINGS.match("clock_style")))
html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("the window's Appearance offers it, and its times read it",
      'id="ap-clock"' in html and "hour12: clockHour12()" in html and "saveSetting('clock_style'" in html)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all clock checks passed")
