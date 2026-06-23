"""Computer-use: real mouse + keyboard control via the Windows API (ctypes).

No third-party dependency (no pyautogui) — pure ctypes, so it works in the
packaged build with nothing to install. `SetCursorPos` moves the REAL OS cursor,
so the user literally watches it glide to the target (that's the visible cursor,
no overlay needed). Windows-only; other OSes report "not supported".

This is the foundation for letting Hearth operate the desktop the way it drives
the browser. The agent should screenshot + view_image first to find coordinates,
then move/click/type. All of it is gated behind the risky-tool permission prompt.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

_WIN = sys.platform == "win32"

if _WIN:
    _u = ctypes.windll.user32
    _ME_LEFTDOWN, _ME_LEFTUP = 0x0002, 0x0004
    _ME_RIGHTDOWN, _ME_RIGHTUP = 0x0008, 0x0010
    _ME_MIDDLEDOWN, _ME_MIDDLEUP = 0x0020, 0x0040
    _ME_WHEEL = 0x0800
    _KEYUP, _UNICODE = 0x0002, 0x0004
    _PUL = ctypes.POINTER(ctypes.c_ulong)

    class _KBD(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", _PUL)]

    class _UN(ctypes.Union):
        _fields_ = [("ki", _KBD)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UN)]

_VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "shift": 0x10, "win": 0x5B, "meta": 0x5B,
    **{f"f{i}": 0x70 + (i - 1) for i in range(1, 13)},
}


def supported() -> bool:
    return _WIN


def screen_size():
    if not _WIN:
        return (0, 0)
    return (_u.GetSystemMetrics(0), _u.GetSystemMetrics(1))


def _pos():
    pt = wintypes.POINT()
    _u.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def move(x: int, y: int, duration: float = 0.35):
    """Glide the REAL cursor to (x,y) — interpolated so the user can watch it,
    not teleport. Returns the final position."""
    if not _WIN:
        return (0, 0)
    x, y = int(x), int(y)
    try:
        sx, sy = _pos()
    except Exception:
        sx, sy = x, y
    steps = max(1, int(duration / 0.012))
    for i in range(1, steps + 1):
        _u.SetCursorPos(int(sx + (x - sx) * i / steps),
                        int(sy + (y - sy) * i / steps))
        time.sleep(0.012)
    _u.SetCursorPos(x, y)
    return (x, y)


def click(x=None, y=None, button: str = "left", double: bool = False):
    if not _WIN:
        return
    if x is not None and y is not None:
        move(x, y)
        time.sleep(0.05)
    down, up = {
        "left": (_ME_LEFTDOWN, _ME_LEFTUP),
        "right": (_ME_RIGHTDOWN, _ME_RIGHTUP),
        "middle": (_ME_MIDDLEDOWN, _ME_MIDDLEUP),
    }.get(button, (_ME_LEFTDOWN, _ME_LEFTUP))
    for _ in range(2 if double else 1):
        _u.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        _u.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.06)


def scroll(amount: int):
    """Positive = up, negative = down. One 'notch' ≈ 1 unit."""
    if not _WIN:
        return
    _u.mouse_event(_ME_WHEEL, 0, 0, int(amount) * 120, 0)


def _send_unicode(ch: str):
    code = ord(ch)
    for flags in (_UNICODE, _UNICODE | _KEYUP):
        inp = _INPUT(type=1, u=_UN(ki=_KBD(0, code, flags, 0, None)))
        _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def type_text(text: str):
    if not _WIN:
        return
    for ch in text:
        if ch == "\n":
            press_key("enter")
        elif ch == "\t":
            press_key("tab")
        else:
            _send_unicode(ch)
        time.sleep(0.005)


def _vk(token: str) -> int:
    token = token.strip().lower()
    if token in _VK:
        return _VK[token]
    if len(token) == 1:
        return ord(token.upper())
    return 0


def press_key(key: str):
    """Single named key (enter/tab/esc/f5/...) or a single character."""
    if not _WIN:
        return
    vk = _vk(key)
    if not vk:
        return
    _u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    _u.keybd_event(vk, 0, _KEYUP, 0)


def hotkey(combo: str):
    """A chord like 'ctrl+s', 'alt+tab', 'ctrl+shift+esc', 'win+d'."""
    if not _WIN:
        return
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    vks = [_vk(p) for p in parts]
    vks = [v for v in vks if v]
    if not vks:
        return
    for v in vks:                      # press down in order
        _u.keybd_event(v, 0, 0, 0)
        time.sleep(0.02)
    for v in reversed(vks):            # release in reverse
        _u.keybd_event(v, 0, _KEYUP, 0)
        time.sleep(0.02)
