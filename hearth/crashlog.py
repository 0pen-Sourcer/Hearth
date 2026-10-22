"""A record of the crashes Python can't report.

When native code (an audio driver, a model runtime) brings the process down,
there is no Python error and the program simply vanishes. faulthandler writes
every thread's stack to <workspace>/logs/crash.log at that moment, which names
the call it died in.
"""
from __future__ import annotations

import faulthandler
import os
import time

_FILE = None


def enable(who: str) -> str:
    """Start recording for this process; returns the log's path ('' if it couldn't)."""
    global _FILE
    if _FILE is not None:
        return _FILE.name
    try:
        from .tools import WORKSPACE
        folder = os.path.join(WORKSPACE, "logs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "crash.log")
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".old")
        _FILE = open(path, "a", encoding="utf-8")
        _FILE.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {who} started (pid {os.getpid()}) ---\n")
        _FILE.flush()
        faulthandler.enable(file=_FILE, all_threads=True)
        return path
    except Exception:
        return ""
