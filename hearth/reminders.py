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


def set_reminder(when: str, what: str, recurring: str = "",
                 action_tool: str = "", action_args: Optional[Dict] = None,
                 tag: str = "") -> Dict:
    """Set a reminder. Two flavors:

    Plain — fires a desktop toast + optional TTS at the due time.
    Action — additionally fires a tool call on the same tick. The model can
    use this to say "at 5pm run summarize_emails" and have the tool actually
    execute, not just nudge the user to do it.

    `recurring` (optional) repeats the reminder — e.g. 'every 30 minutes',
    'daily', 'every 2 hours', 'weekly'. After each fire the reminder re-arms
    itself for the next interval.

    `action_tool` (optional) names any Hearth tool. `action_args` carries
    its arguments. The tool's return value is appended to the toast body so
    the user sees the result. Action reminders are the killer differentiator

    `tag` (optional) is a free-form label for grouping/filtering in the GUI.
    """
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
    if action_tool:
        new["action_tool"] = action_tool
        new["action_args"] = action_args or {}
    if tag:
        new["tag"] = tag.strip()
    items.append(new)
    _save_all(items)
    return {"ok": True, "reminder": new}


def snooze_reminder(rid: str, minutes: int = 10) -> Dict:
    """Push a reminder's due time forward by N minutes. Works on fired
    one-shots too (resurrects them as un-fired). Avoids the "I have to
    re-create the reminder I just dismissed" friction.

    Returns {ok: True, new_due_iso} or {ok: False, error}.
    """
    if minutes <= 0:
        return {"ok": False, "error": "minutes must be > 0"}
    items = _load_all()
    for r in items:
        if r.get("id") == rid:
            base = datetime.now()
            new_due = base + timedelta(minutes=int(minutes))
            r["due_iso"] = new_due.isoformat(timespec="seconds")
            r["fired"] = False  # resurrect if it had already fired
            r.pop("fired_at", None)
            r["snoozed_at"] = datetime.now().isoformat(timespec="seconds")
            r["snooze_count"] = int(r.get("snooze_count", 0)) + 1
            _save_all(items)
            return {"ok": True, "id": rid, "new_due_iso": r["due_iso"],
                    "snooze_count": r["snooze_count"]}
    return {"ok": False, "error": f"no reminder with id {rid}"}


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


def _run_action(action_tool: str, action_args: Dict) -> str:
    """Execute a reminder's bound tool. Returns the FULL result text. The
    caller truncates for the toast body but pushes the full text into chat
    so the user actually sees what the action found, not a 240-char stub."""
    if not action_tool:
        return ""
    try:
        from . import execute_tool
        result = execute_tool(action_tool, action_args or {})
        return str(result) if result is not None else "(ok)"
    except Exception as e:
        return f"(action failed: {type(e).__name__}: {e})"


def _prune_old_fired(items: List[Dict], days: int = 30) -> List[Dict]:
    """Drop one-shot reminders that fired more than N days ago. Keeps the
    list small without losing recently-fired entries the user might want
    to inspect. Recurring reminders are never pruned."""
    if not items:
        return items
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    out = []
    for r in items:
        if (r.get("fired") and not r.get("recurring_seconds")
                and (r.get("fired_at") or "") < cutoff):
            continue
        out.append(r)
    return out


def start_watcher(notify: Callable[[str, str], None]) -> None:
    """Begin firing reminders. `notify(title, body)` is called per fire.

    On startup, scans for already-due reminders (catch-up: Hearth was off
    when they should have fired) and fires them with a "while you were
    away" prefix so the user sees what they missed instead of silent drops.

    Action reminders execute their bound tool inline; the tool's return
    value is appended to the toast body. Tool exceptions never crash the
    watcher.
    """
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return
    _watcher_stop.clear()

    def _fire(r: Dict, missed: bool = False) -> None:
        body = r.get("what", "(empty)")
        action_tool = r.get("action_tool", "")
        full_result = ""
        if action_tool:
            full_result = _run_action(action_tool, r.get("action_args") or {})
            if full_result:
                tail = full_result[:240].rstrip()
                if len(full_result) > 240:
                    tail += "..."
                body = f"{body}\n> {tail}"
        title = "Hearth - while you were away" if missed else "Hearth reminder"
        try:
            notify(title, body)
        except Exception:
            pass
        # Push the FULL action result into the chat queue so it surfaces in
        # the conversation on the next turn (or the GUI idle banner), not
        # just the toast. Without this an action reminder runs invisibly —
        # the tool fires but the user never sees what it found.
        if action_tool and full_result:
            try:
                from . import subagents
                subagents.enqueue_notification(
                    source="reminder",
                    name=r.get("what", "")[:60],
                    status="completed",
                    result_text=full_result,
                    summary=f"reminder fired: {r.get('what', '')}"[:160],
                )
            except Exception:
                pass

    def _advance_after_fire(r: Dict, now: datetime, now_iso: str) -> None:
        rec_s = r.get("recurring_seconds")
        if rec_s and isinstance(rec_s, (int, float)):
            next_due = now + timedelta(seconds=rec_s)
            r["due_iso"] = next_due.isoformat(timespec="seconds")
            r["last_fired_at"] = now_iso
        else:
            r["fired"] = True
            r["fired_at"] = now_iso

    def _loop():
        # Catch-up pass: any reminder due in the past that never fired gets
        # fired NOW and marked missed. Recurring reminders advance to the
        # next slot relative to NOW (so a weekly that missed 3 cycles
        # doesn't fire 3 toasts in a row - fires once, next slot is one
        # interval from now). Also prunes one-shots older than 30 days.
        try:
            now = datetime.now()
            now_iso = now.isoformat(timespec="seconds")
            items = _prune_old_fired(_load_all())
            dirty = False
            for r in items:
                if r.get("fired"):
                    continue
                if r.get("due_iso", "") < now_iso:
                    _fire(r, missed=True)
                    _advance_after_fire(r, now, now_iso)
                    dirty = True
            _save_all(items)  # save even if not dirty - pruning may have changed shape
            del dirty
        except Exception:
            pass

        while not _watcher_stop.is_set():
            try:
                now = datetime.now()
                now_iso = now.isoformat(timespec="seconds")
                items = _load_all()
                dirty = False
                for r in items:
                    if r.get("fired"):
                        continue
                    if r.get("due_iso", "") <= now_iso:
                        _fire(r)
                        _advance_after_fire(r, now, now_iso)
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
