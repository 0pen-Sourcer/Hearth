"""A record of the crashes Python can't report, and a word about them next time.

When native code (an audio driver, a model runtime) brings the process down,
there is no Python error and the program simply vanishes. faulthandler writes
every thread's stack to <workspace>/logs/crash.log at that moment, which names
the call it died in.

Each process that records also notes where its stretch of the log starts and
whether it ended cleanly. At the next start, a stretch that didn't end cleanly
and has a stack in it is a crash: it is kept as pending until they send it or
dismiss it. A process that was killed or lost to a power cut leaves no stack,
so it says nothing.
"""
from __future__ import annotations

import faulthandler
import json
import os
import platform
import re
import sys
import time
from typing import Dict, Optional
from urllib.parse import quote

_FILE = None
_WHO = ""

# What faulthandler writes when it records a crash.
_STACK_MARKS = ("Fatal Python error", "fatal exception", "Current thread 0x", "Thread 0x")
ISSUES_URL = "https://github.com/0pen-Sourcer/Hearth/issues/new"
_URL_MAX = 7000     # browsers and GitHub cut a longer address


def _folder() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, "logs")


def _state_path() -> str:
    return os.path.join(_folder(), "crash_state.json")


def _pending_path() -> str:
    return os.path.join(_folder(), "crash_pending.json")


def _read_json(path: str) -> Dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, d: Dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def scrub(text: str) -> str:
    """The stack without the person: their home folder and account name go."""
    home = os.path.expanduser("~")
    user = os.path.basename(home.rstrip("\\/"))
    out = text
    for h in sorted({home, home.replace("\\", "/"), home.replace("/", "\\")}, key=len, reverse=True):
        if h and len(h) > 1:
            out = out.replace(h, "~")
    out = re.sub(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"']+", r"\1<user>", out)
    out = re.sub(r"(/home/|/Users/)[^/\s\"']+", r"\1<user>", out)
    if user and len(user) > 2:
        out = re.sub(re.escape(user), "<user>", out, flags=re.I)
    return out


def _look_back(who: str, path: str) -> None:
    """Whether the last run of `who` crashed; if so, keep its stack as pending."""
    st = _read_json(_state_path()).get(who) or {}
    if not st or st.get("clean"):
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    start = int(st.get("offset") or 0)
    if start > size:      # the log was set aside since; its tail is in .old
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        f.seek(start)
        tail = f.read(200_000)
    # Only this process's stretch: up to the next process's start line.
    tail = re.split(r"\n--- \d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", tail)[0]
    if not any(m in tail for m in _STACK_MARKS):
        return
    _write_json(_pending_path(), {"at": int(time.time()), "who": who, "version": _version(),
                                  "stack": scrub(tail.strip())[-12000:]})


def _version() -> str:
    try:
        from .updater import HEARTH_VERSION
        return HEARTH_VERSION
    except Exception:
        return ""


def enable(who: str) -> str:
    """Start recording for this process; returns the log's path ('' if it couldn't)."""
    global _FILE, _WHO
    if _FILE is not None:
        return _FILE.name
    try:
        folder = _folder()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "crash.log")
        try:
            if os.path.exists(path):
                _look_back(who, path)
        except Exception:
            pass
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".old")
        _FILE = open(path, "a", encoding="utf-8")
        _FILE.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {who} started (pid {os.getpid()}) ---\n")
        _FILE.flush()
        _WHO = who
        try:
            st = _read_json(_state_path())
            st[who] = {"pid": os.getpid(), "offset": _FILE.tell(), "clean": False}
            _write_json(_state_path(), st)
        except Exception:
            pass
        faulthandler.enable(file=_FILE, all_threads=True)
        import atexit
        atexit.register(clean_exit)
        return path
    except Exception:
        return ""


def clean_exit() -> None:
    """This process is ending on purpose. Called on Quit (which skips atexit) and at exit."""
    if not _WHO:
        return
    try:
        st = _read_json(_state_path())
        if st.get(_WHO, {}).get("pid") == os.getpid():
            st[_WHO]["clean"] = True
            _write_json(_state_path(), st)
    except Exception:
        pass


def pending() -> Optional[Dict]:
    """The crash the last run ended in, until it is sent or dismissed."""
    d = _read_json(_pending_path())
    return d if d.get("stack") else None


def dismiss() -> None:
    try:
        os.remove(_pending_path())
    except OSError:
        pass


def report_url(p: Optional[Dict] = None) -> str:
    """A new GitHub issue, filled in: version, system, and the stack. Nothing is sent
    until they press Submit there."""
    p = p or pending() or {}
    title = "Inglen closed unexpectedly"
    head = f"Inglen {p.get('version') or _version()} on {platform.system()} {platform.release()}\n\nStack:\n"
    stack = p.get("stack") or ""
    while True:
        body = head + "```\n" + stack + "\n```\n"
        url = f"{ISSUES_URL}?title={quote(title)}&body={quote(body)}"
        if len(url) <= _URL_MAX or not stack:
            return url
        # Keep the end, where the call it died in is; drop whole lines from the top.
        lines = stack.splitlines()
        stack = "\n".join(lines[max(1, len(lines) // 5):]) if len(lines) > 1 else stack[len(stack) // 2:]
