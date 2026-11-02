"""Reminders — file-backed scheduler.

Stored at ~/Jarvis/reminders.json. A background watcher (started by web.py)
fires desktop notifications when reminders come due.

Usage from a tool call:
    set_reminder(when="2026-05-27T09:00", what="standup")
    set_reminder(when="25m", what="check the oven")

`when` is a local ISO timestamp or a duration from now. The model converts what
the user said, using the local time it is given every turn.
"""

from __future__ import annotations

import json
from . import env as _env
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from .tools import WORKSPACE, atomic_write_json

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
    except OSError:
        return []
    except json.JSONDecodeError:
        # Saving over it would erase every reminder in it, so it is set aside
        # where it can still be recovered.
        try:
            os.replace(REMINDERS_PATH, REMINDERS_PATH + f".corrupt-{int(time.time())}")
        except OSError:
            pass
        return []


def _save_all(items: List[Dict]) -> None:
    try:
        atomic_write_json(REMINDERS_PATH, items, indent=2)
    except OSError:
        pass


# The window and the terminal each run a watcher over the same file, and either
# can set or cancel a reminder at any moment. Every load-change-save holds this
# lock, across processes, so one never writes back a copy that predates the
# other's change and each due reminder is claimed by exactly one of them.
_THREAD_LOCK = threading.Lock()
_LOCK_WAIT_S = 10.0


@contextmanager
def _store_lock():
    with _THREAD_LOCK:
        # Beside the file it guards, wherever that has been pointed.
        lock_path = os.path.join(os.path.dirname(REMINDERS_PATH) or ".", ".reminders.lock")
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_file(fd)
            try:
                yield
            finally:
                _unlock_file(fd)
        finally:
            os.close(fd)


if os.name == "nt":
    import msvcrt

    def _lock_file(fd: int) -> None:
        deadline = time.monotonic() + _LOCK_WAIT_S
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("the reminders file is locked by another Inglen process")
                time.sleep(0.05)

    def _unlock_file(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_file(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# When a reminder is for
# ---------------------------------------------------------------------------
# The model turns what was said into a time. It is given the exact local time
# every turn and understands language; a pattern list does neither, and a guess
# that reads "tonight at 11" as eleven in the morning is worse than a refusal.
# So this reads only forms that mean one thing: a timestamp, or a duration from
# now. Anything else is refused with instructions, and the model converts it.

_DURATION = re.compile(
    r"^(?:in\s+)?(\d+(?:\.\d+)?)\s*"
    r"(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?|w|weeks?)$",
    re.I,
)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_when(s: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """A timestamp, or a duration from now. None for anything else.

    Timestamps are ISO 8601 in local time ("2026-09-24T23:00"); a trailing
    offset is honoured and converted to local. Durations are a number and a
    unit ("25m", "2h", "in 25 minutes", "1.5 hours").
    """
    s = (s or "").strip()
    if not s:
        return None
    now = now or datetime.now()
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone().replace(tzinfo=None)
        return t
    except ValueError:
        pass
    m = _DURATION.match(s)
    if m:
        unit = m.group(2).lower()[0]
        return now + timedelta(seconds=float(m.group(1)) * _UNIT_SECONDS[unit])
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


def describe_due(target: datetime, now: Optional[datetime] = None) -> str:
    """'tomorrow, Wed 30 Sep at 00:00, 1 h 34 min from now'."""
    now = now or datetime.now()
    days = (target.date() - now.date()).days
    day = {0: "today", 1: "tomorrow"}.get(days, "")
    stamp = f"{target:%a %d %b} at {target:%H:%M}"
    mins = max(0, round((target - now).total_seconds() / 60))
    if mins < 60:
        gap = f"{mins} min"
    elif mins < 48 * 60:
        gap = f"{mins // 60} h {mins % 60} min" if mins % 60 else f"{mins // 60} h"
    else:
        gap = f"{round(mins / 1440)} days"
    return f"{day + ', ' if day else ''}{stamp}, {gap} from now"


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
    the user sees the result.

    `tag` (optional) is a free-form label for grouping/filtering in the GUI.
    """
    now = datetime.now()
    target = parse_when(when, now)
    if target is None:
        return {"ok": False, "error": (
            f"Not set: '{when}' is not a time I read. Work it out from the current "
            f"local time ({now:%Y-%m-%d %H:%M}, {now:%A}) and send `when` as an ISO "
            f"local timestamp like '{now:%Y-%m-%d}T23:00', or a duration like '25m', "
            f"'2h', '3d'.")}
    # A time already gone is a conversion slip, not a request to fire at once.
    if target < now - timedelta(seconds=60):
        return {"ok": False, "error": (
            f"Not set: {target:%Y-%m-%d %H:%M} has already passed. It is now "
            f"{now:%Y-%m-%d %H:%M} ({now:%A}). Send the next occurrence instead.")}
    new = {
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
    with _store_lock():
        items = _load_all()
        # Unique even when a delete brings the count back to one already used
        # in the same millisecond; two reminders sharing an id means cancelling
        # one cancels the other.
        _existing = {r.get("id") for r in items}
        _ms = int(time.time() * 1000)
        _rid = f"r_{_ms}_{uuid.uuid4().hex[:6]}"
        while _rid in _existing:
            _rid = f"r_{_ms}_{uuid.uuid4().hex[:6]}"
        new = {"id": _rid, **new}
        items.append(new)
        _save_all(items)
    # Stated in words with the gap from now, so a conversion slip (midnight
    # stored for "tonight at 11") is visible next to what the reply claims.
    return {"ok": True, "set_for": describe_due(target, now), "reminder": new}


def snooze_reminder(rid: str, minutes: int = 10) -> Dict:
    """Push a reminder's due time forward by N minutes. Works on fired
    one-shots too (resurrects them as un-fired). Avoids the "I have to
    re-create the reminder I just dismissed" friction.

    Returns {ok: True, new_due_iso} or {ok: False, error}.
    """
    if minutes <= 0:
        return {"ok": False, "error": "minutes must be > 0"}
    with _store_lock():
        items = _load_all()
        for r in items:
            if r.get("id") == rid:
                new_due = datetime.now() + timedelta(minutes=int(minutes))
                r["due_iso"] = new_due.isoformat(timespec="seconds")
                r["fired"] = False  # resurrect if it had already fired
                r.pop("fired_at", None)
                r["snoozed_at"] = datetime.now().isoformat(timespec="seconds")
                r["snooze_count"] = int(r.get("snooze_count", 0)) + 1
                _save_all(items)
                return {"ok": True, "id": rid, "new_due_iso": r["due_iso"],
                        "snooze_count": r["snooze_count"]}
    return {"ok": False, "error": f"no reminder with id {rid}"}


def update_reminder(rid: str, what: Optional[str] = None, when: Optional[str] = None,
                    recurring: Optional[str] = None, until: Optional[str] = None) -> Dict:
    """Change what a reminder says, when it is due, or how it repeats ("" makes it
    a one-off), and the last day a repeating one comes due (`until`, a date;
    "" for never). Read the same way set_reminder reads them; a changed one that
    had already come due is due again."""
    now = datetime.now()
    target = None
    if when is not None:
        target = parse_when(when, now)
        if target is None:
            return {"ok": False, "error": f"'{when}' is not a time I read"}
        if target < now - timedelta(seconds=60):
            return {"ok": False, "error": f"{target:%Y-%m-%d %H:%M} has already passed"}
    sec = None
    if recurring:
        sec = _parse_recurring(recurring)
        if sec is None:
            return {"ok": False, "error": f"'{recurring}' is not a repeat I read"}
    with _store_lock():
        items = _load_all()
        for r in items:
            if r.get("id") != rid:
                continue
            if what is not None and what.strip():
                r["what"] = what.strip()
            if target is not None:
                r["due_iso"] = target.isoformat(timespec="seconds")
                r["when_text"] = when
                r["fired"] = False
                r.pop("fired_at", None)
            if recurring is not None:
                if recurring:
                    r["recurring_text"], r["recurring_seconds"] = recurring, sec
                else:
                    r.pop("recurring_text", None)
                    r.pop("recurring_seconds", None)
            if until is not None:
                if until.strip():
                    r["until_iso"] = until.strip()[:10] + "T23:59:59"
                else:
                    r.pop("until_iso", None)
            _save_all(items)
            due = datetime.fromisoformat(r["due_iso"])
            return {"ok": True, "id": rid, "set_for": describe_due(due, now), "reminder": dict(r)}
    return {"ok": False, "error": f"no reminder with id {rid}"}


def pause_reminder(rid: str, paused: bool = True) -> Dict:
    """Hold a reminder without losing it, or let it go again. A repeating one
    resumes at its next slot rather than firing for the ones it skipped."""
    with _store_lock():
        items = _load_all()
        for r in items:
            if r.get("id") != rid:
                continue
            if paused:
                r["paused"] = True
            else:
                r.pop("paused", None)
                rec_s = r.get("recurring_seconds")
                if rec_s and isinstance(rec_s, (int, float)) and rec_s > 0:
                    try:
                        due = datetime.fromisoformat(r.get("due_iso", ""))
                    except Exception:
                        due = datetime.now()
                    step = timedelta(seconds=max(1.0, float(rec_s)))
                    while due <= datetime.now():
                        due += step
                    r["due_iso"] = due.isoformat(timespec="seconds")
            _save_all(items)
            return {"ok": True, "id": rid, "paused": bool(r.get("paused")), "due_iso": r.get("due_iso")}
    return {"ok": False, "error": f"no reminder with id {rid}"}


def done_reminder(rid: str) -> Dict:
    """They've seen it: its chat is marked read and it is kept as done. A
    repeating one still comes back at its next time."""
    with _store_lock():
        items = _load_all()
        for r in items:
            if r.get("id") == rid:
                r["done_at"] = datetime.now().isoformat(timespec="seconds")
                _save_all(items)
                break
        else:
            return {"ok": False, "error": f"no reminder with id {rid}"}
    try:
        from . import chat_sync
        chat_sync.seen(chat_id_for({"id": rid}))
    except Exception:
        pass
    return {"ok": True, "id": rid}


def _done_since(rid: str, since_iso: str) -> bool:
    for r in _load_all():
        if r.get("id") == rid:
            return (r.get("done_at") or "") >= since_iso
    return False


def list_reminders(include_fired: bool = False) -> List[Dict]:
    items = _load_all()
    if not include_fired:
        items = [r for r in items if not r.get("fired")]
    items.sort(key=lambda r: r.get("due_iso", ""))
    return items


def cancel_reminder(rid: str) -> bool:
    with _store_lock():
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


def phone_targets() -> Dict[str, Any]:
    """Where a notice for the phone would go now: the Inglen app on paired phones
    (Plus, through the relay) and the chat apps connected with someone let in."""
    out: Dict[str, Any] = {"app": 0, "chat_apps": []}
    try:
        from . import relay_bridge
        out["app"] = len(relay_bridge.phones())
    except Exception:
        pass
    try:
        from . import channel_core
        out["chat_apps"] = [ch for ch in ("telegram", "discord")
                            if channel_core.config_of(ch).get("bot_token") and channel_core.owner_of(ch)]
    except Exception:
        pass
    return out


def _push_phone(title: str, body: str, chat: str = "") -> bool:
    """With "send reminders to my phone" on (Settings > Behavior > Notifications),
    a notice also goes to the Inglen app on paired phones and to the chat app
    they connected. Never raises."""
    try:
        from .tools import WORKSPACE as _WS
        with open(os.path.join(_WS, "settings.json"), encoding="utf-8") as _sf:
            if not json.load(_sf).get("phone_reminders"):
                return False
        where = phone_targets()
        sent = False
        if where["app"]:
            # The phone bridge passes it on as a notification (relay_bridge.on_window_event).
            from . import live_bus
            live_bus.publish({"kind": "phone_notice", "title": title or "Inglen", "body": body, "c": chat})
            sent = True
        if where["chat_apps"]:
            from . import channel_core
            sent = channel_core.push_any(f"{title}: {body}" if title and title != "Inglen" else body) or sent
        return sent
    except Exception:
        return False


_APP_ID = "Hearth.Assistant"
_identity_ready = False


def _find_icon() -> Optional[str]:
    """Hearth's icon in the saved look, else the shipped one, both in a
    PyInstaller bundle and from source: an .ico for Windows' notices, a .png
    for the notices of Linux and macOS desktops, as the window uses."""
    import sys
    ext = "ico" if sys.platform == "win32" else "png"
    try:
        from . import look_icon
        f = look_icon.file(*look_icon.saved(), ext)
        if f:
            return f
    except Exception:
        pass
    cands = []
    name = "icon." + ext
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        exe_dir = os.path.dirname(sys.executable)
        cands += [os.path.join(base, "assets", name),
                  os.path.join(exe_dir, "assets", name),
                  os.path.join(exe_dir, "_internal", "assets", name)]
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(os.path.dirname(here), "assets", name),
              os.path.join(here, "assets", name)]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _ensure_app_identity() -> Optional[str]:
    """Give toasts a real identity so Windows shows 'Hearth' + the flame icon
    instead of 'Hearth.exe' with a blank tile. Sets an explicit
    AppUserModelID for this process and registers it under HKCU with a display
    name + icon. Best-effort, runs once; returns the icon path (for backends
    that take one directly). Cached so repeated reminders don't re-register."""
    global _identity_ready
    icon = _find_icon()
    if _identity_ready:
        return icon
    _identity_ready = True
    import sys
    if sys.platform != "win32":
        return icon
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_ID)
    except Exception:
        pass
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\AppUserModelId\\" + _APP_ID) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Inglen")
            # The notification header takes an image file; an .ico shows as the
            # default window icon, so the PNG beside it is named here.
            png = icon[:-4] + ".png" if icon and icon.lower().endswith(".ico") else icon
            if png and os.path.isfile(png):
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, png)
            elif icon:
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon)
    except Exception:
        pass
    return icon


# Win32 NOTIFYICONDATAW limits: szInfoTitle 64, szInfo 256, both including the
# terminator. Kept a little under so a multi-byte character near the edge
# cannot push it over.
_TOAST_TITLE_MAX = 60
_TOAST_BODY_MAX = 250


def _fit_toast(title: str, body: str) -> tuple:
    """Clamp a toast to what the OS notifier accepts."""
    _ell = chr(0x2026)
    t = (title or "").strip()
    for _ch in (chr(13), chr(10), chr(9)):
        t = t.replace(_ch, " ")
    b = (body or "").strip()
    if len(t) > _TOAST_TITLE_MAX:
        t = t[:_TOAST_TITLE_MAX - 1].rstrip() + _ell
    if len(b) > _TOAST_BODY_MAX:
        b = b[:_TOAST_BODY_MAX - 1].rstrip() + _ell
    return t, b


# Hearth's own tray icon, when this process has one. A notice shown through it
# adds nothing to the tray; every other way adds an icon of its own, and one
# that isn't taken down cleanly stays behind as a ghost until the mouse passes.
_tray_notify: Optional[Callable[[str, str], None]] = None
_balloons: list = []


def use_tray(fn: Optional[Callable[[str, str], None]]) -> None:
    global _tray_notify
    _tray_notify = fn


def _balloon(title: str, body: str, icon: Optional[str]) -> bool:
    """A notice from a process with no tray icon of its own (the terminal): its
    icon comes down when the notice ends, and at exit if that comes first."""
    try:
        from plyer.platforms.win.libs.balloontip import WindowsBalloonTip  # type: ignore
    except Exception:
        return False

    def _run():
        tip = None
        try:
            tip = WindowsBalloonTip.__new__(WindowsBalloonTip)
            _balloons.append(tip)
            tip.__init__(title=title, message=body, app_name="Inglen", app_icon=icon or "", timeout=10)
        except Exception:
            pass
        finally:
            _take_down(tip)

    threading.Thread(target=_run, daemon=True, name="hearth-notice").start()
    return True


def _take_down(tip) -> None:
    if tip is None:
        return
    try:
        tip.remove_notify()
    except Exception:
        pass
    try:
        _balloons.remove(tip)
    except ValueError:
        pass


def _take_down_all() -> None:
    for tip in list(_balloons):
        _take_down(tip)


import atexit as _atexit  # noqa: E402
_atexit.register(_take_down_all)


# ---------------------------------------------------------------------------
# A due reminder's notice, with Snooze and Done on it
# ---------------------------------------------------------------------------

SNOOZE_MINUTES = 10
SNOOZE_LABEL = f"Snooze {SNOOZE_MINUTES} min"
DONE_LABEL = "Done"
_live_toasts: list = []    # Windows drops a toast's buttons once the toast object is collected
_firing = threading.local()  # .rid: the reminder the watcher is showing on this thread


def on_reminder_action(rid: str, action: str) -> Dict:
    """What a notice's button does: the same as the window's and the phone's."""
    if action == "snooze":
        r = next((x for x in _load_all() if x.get("id") == rid), None)
        if r and r.get("recurring_seconds"):
            # A repeating one keeps its times; the snooze is a one-off of its own.
            return set_reminder(when=f"{SNOOZE_MINUTES}m", what=r.get("what") or "Reminder")
        return snooze_reminder(rid, SNOOZE_MINUTES)
    if action == "done":
        return done_reminder(rid)
    return {"ok": False, "error": f"unknown action {action}"}


def _windows_actions(rid: str, title: str, body: str, icon: Optional[str]) -> bool:
    """A Windows toast in the reminder scenario (it stays up until answered),
    with the two buttons answered in this process."""
    try:
        from windows_toasts import (InteractableWindowsToaster, Toast, ToastButton,  # type: ignore
                                    ToastScenario)
    except Exception:
        return False
    try:
        toaster = InteractableWindowsToaster("Inglen", notifierAUMID=_APP_ID)
        toast = Toast([title, body], scenario=ToastScenario.Reminder)
        toast.AddAction(ToastButton(SNOOZE_LABEL, "snooze"))
        toast.AddAction(ToastButton(DONE_LABEL, "done"))
        if icon and os.path.isfile(icon):
            try:
                from windows_toasts import ToastDisplayImage, ToastImagePosition  # type: ignore
                img = icon[:-4] + ".png" if icon.lower().endswith(".ico") else icon
                if os.path.isfile(img):
                    toast.AddImage(ToastDisplayImage.fromPath(img, position=ToastImagePosition.AppLogo))
            except Exception:
                pass

        def _activated(args) -> None:
            what = str(getattr(args, "arguments", "") or "")
            if what in ("snooze", "done"):
                on_reminder_action(rid, what)

        toast.on_activated = _activated
        _live_toasts.append((toaster, toast))
        del _live_toasts[:-20]
        toaster.show_toast(toast)
        return True
    except Exception:
        return False


def _linux_actions(rid: str, title: str, body: str, icon: Optional[str]) -> bool:
    """notify-send with two actions (libnotify 0.7.9 and later): it prints the
    one pressed. A desktop that shows no buttons still shows the notice."""
    import shutil
    import subprocess
    exe = shutil.which("notify-send")
    if not exe:
        return False
    try:
        helptext = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    if "--action" not in helptext:
        return False
    cmd = [exe, "--app-name=Inglen", "--urgency=critical", "--wait",
           f"--action=snooze={SNOOZE_LABEL}", f"--action=done={DONE_LABEL}"]
    if icon:
        cmd.append(f"--icon={icon}")
    cmd += [title, body]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return False

    def _wait() -> None:
        try:
            out, _ = proc.communicate(timeout=6 * 3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            return
        what = (out or "").strip().splitlines()[-1:] or [""]
        if what[0] in ("snooze", "done"):
            on_reminder_action(rid, what[0])

    threading.Thread(target=_wait, daemon=True, name="hearth-reminder-notice").start()
    return True


def notify_reminder(rid: str, title: str, body: str) -> bool:
    """Show a due reminder with Snooze and Done where this desktop can. False
    when it can't, and the caller shows the plain notice instead."""
    title, body = _fit_toast(title, body)
    icon = _ensure_app_identity()
    if sys.platform == "win32":
        return _windows_actions(rid, title, body, icon)
    if sys.platform.startswith("linux"):
        return _linux_actions(rid, title, body, icon)
    return False


def desktop_notify(title: str, body: str, reminder_id: Optional[str] = None) -> bool:
    """Pop a desktop notification now. Tries plyer (cross-platform) then
    win10toast (Windows), falling back to stdout + TTS. Also sends it to the
    phone app they connected when they asked for that. Returns True if a real
    DESKTOP toast fired. Shared by the
    reminder watcher AND the `notify` tool. A due reminder (its id given, or
    the watcher firing it on this thread) gets Snooze and Done where the
    desktop can show them, else the plain notice."""
    rid = reminder_id or getattr(_firing, "rid", None)
    chat = chat_id_for({"id": rid}) if rid else ""      # a tap on the phone opens the reminder's chat
    if rid:
        try:
            if notify_reminder(rid, title, body):
                _push_phone(title, body, chat)
                return True
        except Exception:
            pass
    _push_phone(title, body, chat)  # phone push gets the full text, it has no limit
    # The Windows tray-balloon struct caps the body at 256 chars and the title
    # at 64. Overshooting raises inside the notifier's own worker thread, which
    # no try/except here can catch, so the toast just never appeared. Trim first.
    title, body = _fit_toast(title, body)
    if _tray_notify is not None:
        try:
            _tray_notify(title, body)
            return True
        except Exception:
            pass
    icon = _ensure_app_identity()
    # When we have the .ico, prefer win10toast FIRST — its `icon_path` actually
    # renders our icon on the toast. plyer's Windows backend usually shows a
    # generic/default icon, which is why reminders looked unbranded. plyer stays
    # as the cross-platform fallback (and primary on macOS/Linux).
    if icon and os.path.isfile(icon):
        try:
            from win10toast import ToastNotifier  # type: ignore
            ToastNotifier().show_toast(title, body, duration=8, threaded=True,
                                       icon_path=icon)
            return True
        except Exception:
            pass
    import sys
    if sys.platform == "win32" and _balloon(title, body, icon):
        return True
    try:
        from plyer import notification  # type: ignore
        kw = {"title": title, "message": body, "app_name": "Inglen", "timeout": 10}
        if icon:
            kw["app_icon"] = icon
        notification.notify(**kw)
        return True
    except Exception:
        pass
    try:
        from win10toast import ToastNotifier  # type: ignore
        ToastNotifier().show_toast(title, body, duration=8, threaded=True,
                                   icon_path=icon or None)
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


# Action-reminders whose tool changes state or runs arbitrary code must NOT
# auto-fire silently from the background watcher — when one comes due, Hearth
# surfaces it and ASKS the user to confirm first (and the normal risky-tool
# permission prompt still gates the actual call). Read-only actions (web_search,
# etc.) keep firing inline so a "check X every morning" reminder still just works.
_CONFIRM_ON_FIRE = {
    "run_command", "write_file", "edit_file", "create_directory", "delete_path",
    "move_path", "open_app", "open_url", "open_in_browser", "send_email",
    "computer_click", "computer_type", "computer_key", "computer_scroll",
    "computer_drag", "computer_move", "desktop_click", "desktop_type",
    "execute_actions",
    "browse", "browse_click", "browse_type", "browse_scroll",
    "generate_image", "generate_video", "create_plugin", "delete_plugin",
    "install_skill", "memory_forget", "set_up", "undo",
}


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


def _advance_after_fire(r: Dict, now: datetime, now_iso: str) -> None:
    rec_s = r.get("recurring_seconds")
    if rec_s and isinstance(rec_s, (int, float)) and rec_s > 0:
        # Advance on the original phase, stepping the due time by the interval
        # until it is in the future, so "daily at 9am" stays at 9am instead of
        # drifting to launch time plus a day, which piled reminders set for
        # different times onto one instant.
        try:
            next_due = datetime.fromisoformat(r.get("due_iso", ""))
        except Exception:
            next_due = now
        step = timedelta(seconds=max(1.0, float(rec_s)))
        while next_due <= now:
            next_due += step
        r["last_fired_at"] = now_iso
        if r.get("until_iso") and next_due.isoformat(timespec="seconds") > r["until_iso"]:
            r["fired"] = True
            r["fired_at"] = now_iso
            return
        r["due_iso"] = next_due.isoformat(timespec="seconds")
    else:
        r["fired"] = True
        r["fired_at"] = now_iso


def _claim_due(missed: bool = False) -> List[Dict]:
    """Take the reminders that are due, marking them fired or advanced in the
    same locked write, and return them to fire. Whichever process claims one
    fires it; the other finds it already taken. Firing happens after the lock
    is released, since an action can run a tool for seconds."""
    with _store_lock():
        now = datetime.now()
        now_iso = now.isoformat(timespec="seconds")
        items = _load_all()
        pruned = _prune_old_fired(items) if missed else items
        due = []
        for r in pruned:
            if r.get("fired") or r.get("paused"):
                continue
            if r.get("due_iso", "") <= now_iso:
                due.append(dict(r))
                _advance_after_fire(r, now, now_iso)
        if due or len(pruned) != len(items):
            _save_all(pruned)
    return due


def _when_said(iso: str) -> str:
    """A due time as people say it: '9:30 AM', or 'Mon 9:30 AM' on another day."""
    try:
        d = datetime.fromisoformat(iso)
    except Exception:
        return ""
    clock = d.strftime("%I:%M %p").lstrip("0")
    return clock if d.date() == datetime.now().date() else d.strftime("%a ") + clock


def chat_id_for(r: Dict) -> str:
    return "rem_" + str(r.get("id") or "")


def _idle_elsewhere() -> Optional[float]:
    """A Mac's input idle time from IOKit; on Linux, GNOME's idle monitor, then
    xprintidle for X11 desktops."""
    import re
    import shutil
    import subprocess
    runs = []
    if sys.platform == "darwin":
        runs.append((["ioreg", "-c", "IOHIDSystem", "-d", "4"], r'"HIDIdleTime"\s*=\s*(\d+)', 1e9))
    else:
        runs.append((["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                      "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                      "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"], r"uint64 (\d+)", 1e3))
        runs.append((["xprintidle"], r"(\d+)", 1e3))
    for cmd, pattern, per_second in runs:
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(pattern, r.stdout or "")
        if r.returncode == 0 and m:
            return int(m.group(1)) / per_second
    return None


def idle_seconds() -> Optional[float]:
    """How long since the last key press or mouse move on this PC. None where
    that can't be read."""
    if sys.platform != "win32":
        return _idle_elsewhere()
    try:
        import ctypes

        class _LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = _LII()
        lii.cbSize = ctypes.sizeof(_LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0)
    except Exception:
        return None


# Repeating reminders that came due while the person was away, by id: they are
# said once, when one comes due with them back, instead of one each time.
_AWAY: Dict[str, int] = {}


def held_while_away(r: Dict) -> bool:
    """A repeating reminder with nothing to run, due while nobody has touched
    the PC for at least its own interval, waits for them instead of speaking to
    an empty room. Counted, so the next one says how many came by."""
    every = r.get("recurring_seconds")
    if not every or r.get("action_tool"):
        return False
    idle = idle_seconds()
    if idle is None or idle < float(every):
        return False
    rid = str(r.get("id") or "")
    _AWAY[rid] = _AWAY.get(rid, 0) + 1
    return True


def deliver(r: Dict, note: str, wait_s: float = 600) -> threading.Thread:
    """JARVIS says a due reminder in that reminder's own chat, never in the chat
    someone has open. Written to the chat file by whichever process fired it, so
    it is there even with the window closed; a repeating one stays in one chat."""
    fired_iso = datetime.now().isoformat(timespec="seconds")

    def _run() -> None:
        from . import chat_sync, headless
        cid = chat_id_for(r)
        what = r.get("what") or "Reminder"
        # A reply already running keeps the model; this one waits its turn.
        end = time.time() + wait_s
        while headless.turn_is_active() and time.time() < end:
            time.sleep(1)
        prior = chat_sync.from_chat((chat_sync.load(cid) or {}).get("messages") or [])
        parts: List[str] = []
        why: List[str] = []

        def emit(kind, **kw):
            if kind == "assistant":
                parts.append(kw.get("content") or "")
            elif kind == "error":
                why.append(str(kw.get("message") or "").strip())
        try:
            import asyncio
            # Nobody is there to approve anything, so nothing that asks gets run.
            # Only its last exchange: with all the earlier ones each reminder
            # echoed the one before ("your back's been waiting on this one").
            asyncio.run(headless.run_once(note, emit=emit, history=prior[-2:],
                                          permission_check=lambda *_a: "deny", chat_id=cid))
        except Exception:
            pass
        text = "".join(parts).strip()
        if not text:
            text = f"Reminder: {what}"
            if why and why[-1]:
                text += f"\n\n(I couldn't say it in my own words: {why[-1].rstrip('.')}.)"
        chat_sync.post(cid, what[:60], "reminder",
                       [{"role": "assistant", "content": text, "via": "reminder",
                         "at": int(time.time() * 1000)}],
                       unread=not _done_since(str(r.get("id") or ""), fired_iso))
    t = threading.Thread(target=_run, daemon=True, name="hearth-reminder-chat")
    t.start()
    return t


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
        if not missed and held_while_away(r):
            return
        away = _AWAY.pop(str(r.get("id") or ""), 0)
        body = r.get("what", "(empty)")
        action_tool = r.get("action_tool", "")
        action_args = r.get("action_args") or {}
        # Anything gated for permission in an ordinary turn is gated here too.
        # Reading only this module's own list let a scheduled start_job fire
        # an arbitrary shell command at the due time with no prompt, because
        # the list had never been told start_job existed.
        try:
            from .tools import is_risky as _is_risky
        except Exception:
            _is_risky = lambda _n, _a=None: True   # fail closed
        # With its arguments: a read of a place that holds keys asks whatever the tool.
        needs_confirm = bool(action_tool) and (
            action_tool in _CONFIRM_ON_FIRE or _is_risky(action_tool, action_args))
        full_result = ""
        if action_tool and not needs_confirm:
            full_result = _run_action(action_tool, action_args)
            if full_result:
                tail = full_result[:240].rstrip()
                if len(full_result) > 240:
                    tail += "..."
                body = f"{body}\n> {tail}"
        title = "Inglen - while you were away" if missed else "Inglen reminder"
        toast_body = body
        if needs_confirm:
            toast_body = f"{body}\n(wants to run {action_tool}; open Inglen to confirm)"
        # The caller's notify always runs (the terminal prints it into the
        # chat); desktop_notify inside it sees which reminder is firing.
        _firing.rid = str(r.get("id") or "") or None
        try:
            notify(title, toast_body)
        except Exception:
            pass
        finally:
            _firing.rid = None
        # The reminder becomes a turn in its own chat, so JARVIS says it in his
        # own words and knows it fired. The notice above covers an empty desk.
        try:
            what = r.get("what", "")
            due = _when_said(r.get("due_iso", ""))
            late = f", due {due} while Inglen was closed" if missed and due else ""
            if needs_confirm:
                try:
                    args_str = json.dumps(action_args, ensure_ascii=False)[:300]
                except Exception:
                    args_str = str(action_args)[:300]
                note = (f'Reminder they set{late}: "{what}". It was set to run {action_tool} '
                        f'with {args_str}. Nobody has approved that yet, so it has not run: '
                        f'say it is due, say what it would do, and ask whether to go ahead. '
                        f'If they say yes, run it then.')
            elif action_tool and full_result:
                note = (f'Reminder they set{late}: "{what}". It ran {action_tool}, which '
                        f'returned:\n{full_result}\nTell them what came of it.')
            else:
                came = (f" It also came due {away} time{'s' if away != 1 else ''} while they were away "
                        f"from the PC; this is the one they see." if away else "")
                note = (f'Reminder they set{late}: "{what}".{came} Remind them in a line or two, '
                        f'in your own words.')
            deliver(r, note)
        except Exception:
            pass
        # Due now (not one missed while Inglen was closed): a call, when they allowed calls for reminders.
        if not missed:
            try:
                from . import call_me
                call_me.request("reminder", r.get("what", "") or "Reminder", chat=chat_id_for(r),
                                key="reminder:" + str(r.get("id") or "") + ":" + str(r.get("due_iso") or ""))
            except Exception:
                pass

    def _loop():
        # The first pass catches up on what came due while Hearth was off and
        # fires it once as missed; a recurring one that missed several slots
        # moves to its next future slot rather than firing once per slot.
        missed = True
        while not _watcher_stop.is_set():
            try:
                for r in _claim_due(missed):
                    _fire(r, missed=missed)
            except Exception:
                pass
            missed = False
            _watcher_stop.wait(20.0)

    _watcher_thread = threading.Thread(target=_loop, daemon=True, name="hearth-reminders")
    _watcher_thread.start()


def stop_watcher() -> None:
    _watcher_stop.set()
