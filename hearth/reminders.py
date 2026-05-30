"""Reminders — file-backed scheduler.

Stored at ~/Jarvis/reminders.json. A background watcher (started by web.py)
fires desktop notifications when reminders come due.

Usage from a tool call:
    set_reminder(when="2026-05-27 09:00", what="standup")
    set_reminder(when="in 25 minutes", what="check the oven")
    set_reminder(when="tomorrow at 7am", what="workout")

The `when` parser handles:
  - ISO timestamps: "2026-05-27T09:00:00", "2026-05-27 09:00"
  - Relative: "in 25 minutes", "in 2 hours", "in 3 days"
  - Natural: "tomorrow at 7am", "tonight at 9pm", "next monday at 10am"
  - Time-only (today): "9pm", "21:00", "7:30am"
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from .tools import WORKSPACE

REMINDERS_PATH = os.path.join(WORKSPACE, "reminders.json")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_all() -> List[Dict]:
    if not os.path.isfile(REMINDERS_PATH):
        return []
    try:
        with open(REMINDERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_all(items: List[Dict]) -> None:
    try:
        with open(REMINDERS_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# When-parser — covers the 80% of user inputs
# ---------------------------------------------------------------------------

_REL_PATTERN = re.compile(
    r"^in\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)$",
    re.I,
)
_TIME_PATTERN = re.compile(r"^(\d{1,2}):?(\d{2})?\s*(am|pm)?$", re.I)
_DAYNAME_PATTERN = re.compile(
    r"^(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+at\s+(.+))?$",
    re.I,
)

_REL_UNITS = {
    "s": 1, "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "m": 60, "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "h": 3600, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]


def _parse_time(s: str, base: datetime) -> Optional[datetime]:
    """Parse "9pm", "21:00", "7:30am" → datetime on the same day as base."""
    m = _TIME_PATTERN.match(s.strip().lower())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hh < 12: hh += 12
    elif ampm == "am" and hh == 12: hh = 0
    try:
        return base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return None


def parse_when(s: str, now: Optional[datetime] = None) -> Optional[datetime]:
    s = (s or "").strip().lower()
    now = now or datetime.now()
    if not s:
        return None

    # ISO-ish: 2026-05-27, 2026-05-27 09:00, 2026-05-27T09:00:00
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.replace("Z", ""), fmt)
        except ValueError:
            pass

    # Relative: "in 25 minutes"
    m = _REL_PATTERN.match(s)
    if m:
        n = float(m.group(1))
        unit = m.group(2).lower()
        seconds = n * _REL_UNITS.get(unit, 60)
        return now + timedelta(seconds=seconds)

    # tomorrow / tonight / today + optional time
    if s.startswith("tomorrow"):
        rest = s[len("tomorrow"):].strip()
        rest = re.sub(r"^at\s+", "", rest)
        base = (now + timedelta(days=1))
        return _parse_time(rest, base) or base.replace(hour=9, minute=0, second=0, microsecond=0)
    if s.startswith("tonight"):
        rest = s[len("tonight"):].strip()
        rest = re.sub(r"^at\s+", "", rest)
        return _parse_time(rest, now) or now.replace(hour=20, minute=0, second=0, microsecond=0)
    if s.startswith("today"):
        rest = s[len("today"):].strip()
        rest = re.sub(r"^at\s+", "", rest)
        return _parse_time(rest, now)

    # "next monday at 10am" / "monday"
    m = _DAYNAME_PATTERN.match(s)
    if m:
        target_day = _DAYS.index(m.group(1).lower())
        today_day = now.weekday()
        diff = (target_day - today_day) % 7
        if diff == 0:
            diff = 7
        base = now + timedelta(days=diff)
        rest = m.group(2) or ""
        return _parse_time(rest, base) or base.replace(hour=9, minute=0, second=0, microsecond=0)

    # Bare time = today
    t = _parse_time(s, now)
    if t:
        # If it's already past, roll to tomorrow
        if t < now:
            t = t + timedelta(days=1)
        return t

    return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

_RECUR_INTERVAL_S = {
    "minute":   60,
    "minutes":  60,
    "hour":   3600,
    "hours":  3600,
    "day":   86400,
    "daily": 86400,
    "week":  86400 * 7,
    "weekly": 86400 * 7,
}


def _parse_recurring(spec: str) -> Optional[int]:
    """Parse a 'recurring' spec to a repeat-interval in seconds. Supports
    'every 30 minutes' / '30m' / 'daily' / 'weekly' / 'hourly' etc. Returns
    None for one-shot reminders."""
    if not spec:
        return None
    s = spec.strip().lower()
    if s in ("hourly", "every hour"):    return 3600
    if s in ("daily", "every day"):      return 86400
    if s in ("weekly", "every week"):    return 86400 * 7
    # 'every N units' or 'N units' or 'Nm/Nh/Nd'
    m = re.match(r"^(?:every\s+)?(\d+)\s*(minute|minutes|min|m|hour|hours|hr|h|day|days|d|week|weeks|w)$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(("m", "min")):  return n * 60
        if unit.startswith(("h", "hr")):   return n * 3600
        if unit.startswith(("d",)):        return n * 86400
        if unit.startswith(("w",)):        return n * 86400 * 7
    return None


def set_reminder(when: str, what: str, recurring: str = "") -> Dict:
    """Set a reminder. `recurring` (optional) makes it repeat - e.g.
    'every 30 minutes', 'daily', 'every 2 hours', 'weekly'. After each fire
    the reminder re-arms itself for the next interval."""
    target = parse_when(when)
    if target is None:
        return {"ok": False, "error": f"Couldn't parse time: '{when}'. Try 'in 25 minutes', '2026-05-27 09:00', 'tomorrow at 7am'."}
    items = _load_all()
    new = {
        "id": f"r_{int(time.time()*1000)}_{len(items)}",
        "when_text": when,
        "due_iso": target.isoformat(timespec="seconds"),
        "what": what,
        "fired": False,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    if recurring:
        sec = _parse_recurring(recurring)
        if sec is None:
            return {"ok": False, "error": f"Couldn't parse recurring spec: '{recurring}'. Try 'every 30 minutes', 'daily', 'every 2 hours', 'weekly'."}
        new["recurring_text"] = recurring
        new["recurring_seconds"] = sec
    items.append(new)
    _save_all(items)
    return {"ok": True, "reminder": new}


def list_reminders(include_fired: bool = False) -> List[Dict]:
    items = _load_all()
    if not include_fired:
        items = [r for r in items if not r.get("fired")]
    items.sort(key=lambda r: r.get("due_iso", ""))
    return items


def cancel_reminder(rid: str) -> bool:
    items = _load_all()
    n = len(items)
    items = [r for r in items if r.get("id") != rid]
    if len(items) == n:
        return False
    _save_all(items)
    return True


# ---------------------------------------------------------------------------
# Background fire loop
# ---------------------------------------------------------------------------

_watcher_thread: Optional[threading.Thread] = None
_watcher_stop = threading.Event()


def desktop_notify(title: str, body: str) -> bool:
    """Pop a desktop notification now. Tries plyer (cross-platform) then
    win10toast (Windows), falling back to stdout + TTS. Returns True if a real
    toast fired. Shared by the reminder watcher AND the `notify` tool."""
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=body, app_name="Hearth", timeout=10)
        return True
    except Exception:
        pass
    try:
        from win10toast import ToastNotifier  # type: ignore
        ToastNotifier().show_toast(title, body, duration=8, threaded=True)
        return True
    except Exception:
        pass
    print(f"[notify] {title}: {body}", flush=True)
    try:
        from . import voice as _v
        if _v.is_available():
            _v.speak(body, blocking=False)
    except Exception:
        pass
    return False


def start_watcher(notify: Callable[[str, str], None]) -> None:
    """Begin firing reminders. `notify(title, body)` is called per fire."""
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return
    _watcher_stop.clear()

    def _loop():
        while not _watcher_stop.is_set():
            try:
                now_iso = datetime.now().isoformat(timespec="seconds")
                items = _load_all()
                dirty = False
                for r in items:
                    if r.get("fired"):
                        continue
                    if r.get("due_iso", "") <= now_iso:
                        try:
                            notify("Hearth reminder", r.get("what", "(empty)"))
                        except Exception:
                            pass
                        # Recurring reminders re-arm themselves; one-shots
                        # get flagged fired so they're skipped next iter.
                        rec_s = r.get("recurring_seconds")
                        if rec_s and isinstance(rec_s, (int, float)):
                            from datetime import timedelta as _td
                            next_due = datetime.now() + _td(seconds=rec_s)
                            r["due_iso"] = next_due.isoformat(timespec="seconds")
                            r["last_fired_at"] = now_iso
                        else:
                            r["fired"] = True
                            r["fired_at"] = now_iso
                        dirty = True
                if dirty:
                    _save_all(items)
            except Exception:
                pass
            _watcher_stop.wait(20.0)  # check every 20s

    _watcher_thread = threading.Thread(target=_loop, daemon=True, name="hearth-reminders")
    _watcher_thread.start()


def stop_watcher() -> None:
    _watcher_stop.set()
