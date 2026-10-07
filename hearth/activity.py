"""What the assistant is doing in another window of Hearth, right now.

The terminal writes a line here as a turn starts, at each tool and when it
ends; the desktop window reads it, so its room shows JARVIS working on a turn
that is running in the terminal. One small file in the workspace, rewritten
whole each time; an entry that hasn't been touched for a while counts as over
(a terminal closed mid-turn never writes "idle").
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

STALE_S = 120


def _path() -> str:
    from .tools import WORKSPACE
    return os.path.join(WORKSPACE, "activity.json")


def note(surface: str, state: str, tool: str = "") -> None:
    """Say what the assistant is doing in this surface: 'thinking',
    'calling <tool>' or 'idle'."""
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"surface": surface, "state": state, "tool": tool, "at": time.time(), "pid": os.getpid()}, f)
        os.replace(tmp, _path())
    except OSError:
        pass


def current(not_from: str = "") -> Optional[Dict[str, Any]]:
    """A turn running in another surface, or None."""
    try:
        with open(_path(), encoding="utf-8") as f:
            a = json.load(f) or {}
    except (OSError, ValueError):
        return None
    if a.get("surface") == not_from or a.get("state") in (None, "", "idle"):
        return None
    if time.time() - float(a.get("at") or 0) > STALE_S:
        return None
    return a
