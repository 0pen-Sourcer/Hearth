"""Computer-use: real mouse + keyboard control via the Windows API (ctypes).

On Windows: no third-party dependency (no pyautogui) — pure ctypes, so it works
in the packaged build with nothing to install. `SetCursorPos` moves the REAL OS
cursor, so the user literally watches it glide to the target (that's the visible
cursor, no overlay needed).

On Linux/macOS: falls back to pynput (driving the same real OS cursor on X11 /
Quartz). EXPERIMENTAL — not yet verified on that hardware; macOS also needs
Accessibility permission granted to the host app. If pynput isn't installed,
`supported()` returns False and the tools report that cleanly.

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

# --- Cross-platform (Linux/macOS) backend via pynput -------------------------
# EXPERIMENTAL, not yet verified on Linux/macOS hardware. On Windows we stay on
# the pure-ctypes path above (no dependency, fully tested). Off Windows we fall
# back to pynput, which drives the REAL OS cursor on X11 / Quartz the same way
# ctypes does on Windows. Guarded import so Windows never needs the package and
# a missing pynput just makes computer-use unavailable (clean message) instead
# of crashing. macOS additionally needs Accessibility permission granted to the
# host app for synthetic input to take effect.
_PYN = False
if not _WIN:
    try:
        from pynput import mouse as _pyn_mouse, keyboard as _pyn_keyboard
        _pyn_m = _pyn_mouse.Controller()
        _pyn_k = _pyn_keyboard.Controller()
        _PYN = True
    except Exception:
        _PYN = False


def supported() -> bool:
    return _WIN or _PYN


def screen_size():
    if _WIN:
        return (_u.GetSystemMetrics(0), _u.GetSystemMetrics(1))
    if _PYN:
        # pynput has no screen-size API; the agent works off screenshot coords
        # anyway, so 0,0 just means "unknown" (callers don't divide by it).
        return (0, 0)
    return (0, 0)


def _pos():
    if _WIN:
        pt = wintypes.POINT()
        _u.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    if _PYN:
        return tuple(int(v) for v in _pyn_m.position)
    return (0, 0)


def move(x: int, y: int, duration: float = 0.35):
    """Glide the REAL cursor to (x,y) — interpolated so the user can watch it,
    not teleport. Returns the final position."""
    if not (_WIN or _PYN):
        return (0, 0)
    x, y = int(x), int(y)
    try:
        sx, sy = _pos()
    except Exception:
        sx, sy = x, y
    steps = max(1, int(duration / 0.012))
    for i in range(1, steps + 1):
        nx = int(sx + (x - sx) * i / steps)
        ny = int(sy + (y - sy) * i / steps)
        if _WIN:
            _u.SetCursorPos(nx, ny)
        else:
            _pyn_m.position = (nx, ny)
        time.sleep(0.012)
    if _WIN:
        _u.SetCursorPos(x, y)
    else:
        _pyn_m.position = (x, y)
    return (x, y)


def _press_button(button: str, down: bool):
    """Low-level mouse button press/release at the current cursor position."""
    if _WIN:
        flags = {
            ("left", True): _ME_LEFTDOWN, ("left", False): _ME_LEFTUP,
            ("right", True): _ME_RIGHTDOWN, ("right", False): _ME_RIGHTUP,
            ("middle", True): _ME_MIDDLEDOWN, ("middle", False): _ME_MIDDLEUP,
        }.get((button, down), _ME_LEFTDOWN if down else _ME_LEFTUP)
        _u.mouse_event(flags, 0, 0, 0, 0)
    elif _PYN:
        btn = {"left": _pyn_mouse.Button.left, "right": _pyn_mouse.Button.right,
               "middle": _pyn_mouse.Button.middle}.get(button, _pyn_mouse.Button.left)
        (_pyn_m.press if down else _pyn_m.release)(btn)


def click(x=None, y=None, button: str = "left", double: bool = False):
    if not (_WIN or _PYN):
        return
    if x is not None and y is not None:
        move(x, y)
        time.sleep(0.05)
    for _ in range(2 if double else 1):
        _press_button(button, True)
        time.sleep(0.03)
        _press_button(button, False)
        time.sleep(0.06)


def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left",
         duration: float = 0.6):
    """Real drag-and-drop: press at (x1,y1), glide to (x2,y2) while held, release.
    Used for moving files/icons, dragging sliders, rearranging tabs, selecting a
    region. The cursor stays visibly down the whole way so the user sees the drag."""
    if not (_WIN or _PYN):
        return
    move(int(x1), int(y1))
    time.sleep(0.08)
    _press_button(button, True)
    time.sleep(0.12)
    move(int(x2), int(y2), duration=max(0.3, duration))  # glide while held
    time.sleep(0.12)
    _press_button(button, False)
    time.sleep(0.05)


def scroll(amount: int):
    """Positive = up, negative = down. One 'notch' ≈ 1 unit."""
    if _WIN:
        _u.mouse_event(_ME_WHEEL, 0, 0, int(amount) * 120, 0)
    elif _PYN:
        _pyn_m.scroll(0, int(amount))


def _send_unicode(ch: str):
    code = ord(ch)
    for flags in (_UNICODE, _UNICODE | _KEYUP):
        inp = _INPUT(type=1, u=_UN(ki=_KBD(0, code, flags, 0, None)))
        _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def type_text(text: str):
    if _WIN:
        for ch in text:
            if ch == "\n":
                press_key("enter")
            elif ch == "\t":
                press_key("tab")
            else:
                _send_unicode(ch)
            time.sleep(0.005)
    elif _PYN:
        _pyn_k.type(text)  # handles unicode + newlines/tabs on X11/Quartz


def _vk(token: str) -> int:
    token = token.strip().lower()
    if token in _VK:
        return _VK[token]
    if len(token) == 1:
        return ord(token.upper())
    return 0


def _pyn_key(token: str):
    """Map a named key/char token to a pynput key object (or the char itself)."""
    token = token.strip().lower()
    special = {
        "enter": "enter", "return": "enter", "tab": "tab", "esc": "esc",
        "escape": "esc", "space": "space", "backspace": "backspace",
        "delete": "delete", "del": "delete", "home": "home", "end": "end",
        "pageup": "page_up", "pagedown": "page_down", "up": "up", "down": "down",
        "left": "left", "right": "right", "ctrl": "ctrl", "control": "ctrl",
        "alt": "alt", "shift": "shift", "win": "cmd", "meta": "cmd",
    }
    if token in special:
        return getattr(_pyn_keyboard.Key, special[token])
    if len(token) == 2 and token[0] == "f" and token[1:].isdigit():
        return getattr(_pyn_keyboard.Key, token, token)
    return token  # single char


def press_key(key: str):
    """Single named key (enter/tab/esc/f5/...) or a single character."""
    if _WIN:
        vk = _vk(key)
        if not vk:
            return
        _u.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
        _u.keybd_event(vk, 0, _KEYUP, 0)
    elif _PYN:
        k = _pyn_key(key)
        _pyn_k.press(k)
        time.sleep(0.02)
        _pyn_k.release(k)


def hotkey(combo: str):
    """A chord like 'ctrl+s', 'alt+tab', 'ctrl+shift+esc', 'win+d'."""
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    if not parts:
        return
    if _WIN:
        vks = [v for v in (_vk(p) for p in parts) if v]
        if not vks:
            return
        for v in vks:                      # press down in order
            _u.keybd_event(v, 0, 0, 0)
            time.sleep(0.02)
        for v in reversed(vks):            # release in reverse
            _u.keybd_event(v, 0, _KEYUP, 0)
            time.sleep(0.02)
    elif _PYN:
        keys = [_pyn_key(p) for p in parts]
        for k in keys:
            _pyn_k.press(k)
            time.sleep(0.02)
        for k in reversed(keys):
            _pyn_k.release(k)
            time.sleep(0.02)
