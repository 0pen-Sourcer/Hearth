"""The clock times are shown in: 12-hour (2:34 PM) or 24-hour (14:34).

One setting, clock_style, read by the window, the phone and every time Inglen writes out:
"12", "24", or "auto" (missing) for the system's own format. Windows says it in its
time format (h or tt is 12-hour, H is 24-hour); where none can be read, 12-hour.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Dict, Optional

STYLES = ("auto", "12", "24")


def system_12h() -> Optional[bool]:
    """Whether the system clock is 12-hour, or None when it can't be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(80)
        # LOCALE_NAME_USER_DEFAULT (None), LOCALE_STIMEFORMAT
        if not ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x1003, buf, len(buf)):
            return None
        fmt = buf.value
    except Exception:
        return None
    if "H" in fmt:
        return False
    if "h" in fmt or "t" in fmt:
        return True
    return None


def _settings() -> Dict[str, Any]:
    # From the file: a reminder firing in a process of its own shouldn't load the window's server.
    from .call_me import _settings as read
    return read()


def style(settings: Optional[Dict[str, Any]] = None) -> str:
    s = _settings() if settings is None else settings
    v = str(s.get("clock_style") or "auto")
    return v if v in STYLES else "auto"


def twelve_hour(settings: Optional[Dict[str, Any]] = None) -> bool:
    v = style(settings)
    if v != "auto":
        return v == "12"
    got = system_12h()
    return True if got is None else got


def hm(t: Any, settings: Optional[Dict[str, Any]] = None, day: bool = False) -> str:
    """A time of day as the clock setting has it: '2:34 PM' or '14:34' (with 'Mon ' first when day)."""
    lt = time.localtime(t) if isinstance(t, (int, float)) else t.timetuple()
    if twelve_hour(settings):
        out = time.strftime("%I:%M %p", lt).lstrip("0")
    else:
        out = time.strftime("%H:%M", lt)
    return (time.strftime("%a ", lt) + out) if day else out
