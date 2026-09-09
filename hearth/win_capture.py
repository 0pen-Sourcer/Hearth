"""Windows capture helpers: keep our own windows out of our own screenshots,
and measure a window's true visual rectangle.

Two problems this solves.

A screenshot taken while the app is in front contains the app. The vision model
then describes the chat it is being asked about, or clicks a control belonging
to the assistant rather than the thing the user asked for. DWM can leave a
window out of every capture surface while still drawing it on the physical
display, which is exactly what is wanted.

That exclusion is applied only for the moment of the capture and then removed.
Left on, it would also hide the window from OBS, Teams and every screen
recorder, so a demo recording would show an empty desktop. The agent must not
see itself; everyone else still must.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import time
from typing import List, Optional, Tuple

_WIN = sys.platform == "win32"

# SetWindowDisplayAffinity. EXCLUDEFROMCAPTURE (Win10 2004+) omits the window
# from capture while still rendering it. MONITOR is the older value and paints
# the captured region black instead, which is worse than not trying.
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# DwmGetWindowAttribute. GetWindowRect includes the invisible resize border DWM
# adds, so a rect taken from it is several pixels too large on every side and
# every coordinate derived from it is off.
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# DWM needs a frame to pick the change up. Measured rather than guessed: below
# about one frame the window is still in the capture.
_AFFINITY_SETTLE_S = 0.06


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _user32():
    return ctypes.windll.user32 if _WIN else None


def own_window_handles(pid: Optional[int] = None) -> List[int]:
    """Top-level visible windows belonging to this process."""
    if not _WIN:
        return []
    pid = pid or os.getpid()
    u = _user32()
    out: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            owner = ctypes.c_ulong()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                out.append(hwnd)
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(_cb, 0)
    except Exception:
        return []
    return out


def set_capture_exclusion(hwnd: int, excluded: bool) -> bool:
    """Hide or restore one window for screen capture. False if unsupported."""
    if not _WIN:
        return False
    try:
        flag = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
        return bool(_user32().SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), flag))
    except Exception:
        return False


@contextlib.contextmanager
def own_windows_hidden(enabled: bool = True):
    """Leave this process's windows out of a capture taken inside the block.

    Restores every window on the way out, including when the capture raises,
    because a window left excluded is invisible to the user's own recorder.
    """
    if not (_WIN and enabled):
        yield []
        return
    changed: List[int] = []
    try:
        for hwnd in own_window_handles():
            if set_capture_exclusion(hwnd, True):
                changed.append(hwnd)
        if changed:
            time.sleep(_AFFINITY_SETTLE_S)
        yield changed
    finally:
        for hwnd in changed:
            set_capture_exclusion(hwnd, False)


def visual_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """A window's rectangle as drawn, without the invisible resize border.

    NOT wired into the capture path, and read this before wiring it. DWM
    answers in PHYSICAL pixels. A process that has not declared itself
    DPI-aware sees virtualized logical pixels everywhere else, so on a scaled
    display the two disagree by the scale factor, measured at 1.2x on a 125%
    monitor here. Feeding this rectangle to a grab that expects logical
    coordinates captures the wrong region entirely. Making the process
    DPI-aware first is the prerequisite.
    """
    if not _WIN:
        return None
    r = _RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(r), ctypes.sizeof(r))
        if hr == 0 and (r.right - r.left) > 0 and (r.bottom - r.top) > 0:
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    try:
        if _user32().GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None
