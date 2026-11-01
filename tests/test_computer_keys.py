"""Keystrokes on Windows, and what a batch does with a control's number.

SendInput takes the size of the whole INPUT struct and refuses anything else, so a
struct that's too short means no key ever arrives while every caller carries on as
if it did.

    .\\.venv\\Scripts\\python.exe tests\\test_computer_keys.py
"""
from __future__ import annotations

import ctypes
import os
import sys

os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import computer as C                                  # noqa: E402
from hearth import desktop_a11y as A                              # noqa: E402
from hearth import tools as T                                     # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------------- the struct itself
if sys.platform == "win32":
    want = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    check("INPUT is the size Windows wants", ctypes.sizeof(C._INPUT) == want)
    empty = C._INPUT(type=1)                     # no key at all: only the struct is tested
    check("and Windows takes it", C._u.SendInput(1, ctypes.byref(empty), ctypes.sizeof(C._INPUT)) == 1)

    # ----------------------------------------------- a refused key is not "typed"
    _send = C._u.SendInput

    class _Refuses:
        def __getattr__(self, n):
            return getattr(ctypes.windll.user32, n)

        def SendInput(self, *a):
            return 0

    saved_u, saved_check, saved_guard = C._u, T._computer_check, T._foreground_guard
    C._u = _Refuses()
    T._computer_check = lambda: (C, None)
    T._foreground_guard = lambda: None
    try:
        out = T._computer_type({"text": "x"})
        check("keys Windows refused are reported, not called typed",
              out.startswith("Error:") and "nothing was typed" in out)
        out = T._computer_key({"key": "ctrl+s"})
        check("same for a shortcut", out.startswith("Error:"))
    finally:
        C._u, T._computer_check, T._foreground_guard = saved_u, saved_check, saved_guard

# --------------------------------------------------- Inglen's own window, by title
if sys.platform == "win32":
    import types
    fake = types.ModuleType("win32gui")
    fake.GetForegroundWindow = lambda: 7
    real = sys.modules.get("win32gui")
    sys.modules["win32gui"] = fake
    T._LAST_FOCUS_HWND = None
    try:
        fake.GetWindowText = lambda h: "Inglen"
        check("typing into Inglen's own window is refused", "Refused" in (T._foreground_guard() or ""))
        fake.GetWindowText = lambda h: "hearth notes.txt - Notepad"
        check("a document with the name in its title isn't", T._foreground_guard() is None)
    finally:
        if real is not None:
            sys.modules["win32gui"] = real
        else:
            del sys.modules["win32gui"]


# -------------------------------------------- a click goes through the pointer path
class _Rect:
    def width(self): return 20

    def height(self): return 10

    def xcenter(self): return 111

    def ycenter(self): return 222


class _Ctrl:
    BoundingRectangle = _Rect()


clicks = []
saved = (A._last, A._raise_owner, C.click)
A._last = [{"backend": "uia", "ctrl": _Ctrl(), "x": 1, "y": 2, "name": "OK", "type": "Button"}]
A._raise_owner = lambda c: None
C.click = lambda x, y, button="left", double=False: clicks.append((x, y, button, double))
try:
    got = A.click(idx=0)
    check("an element's click goes through computer.click, which draws the pointer",
          clicks == [(111, 222, "left", False)] and got == (111, 222))
finally:
    A._last, A._raise_owner, C.click = saved

# ------------------------------------------------------ a batch keeps its controls
# The window renumbers its controls after a step, as a calculator's does whenever
# its display changes.
before = [{"name": "One", "type": "Button"}, {"name": "Two", "type": "Button"},
          {"name": "Three", "type": "Button"}]
after = [{"name": "History", "type": "Button"}] + before
done = []
saved = (A.snapshot, A._last, T._HANDLERS["desktop_click"], A.available)
A._last = list(before)
A.snapshot = lambda *a, **k: (globals().__setitem__("_", None), setattr(A, "_last", list(after)), {})[-1]


def _click(p):
    done.append(A._last[p["idx"]]["name"])
    return "clicked"


T._HANDLERS["desktop_click"] = _click
try:
    out = T._execute_actions({"actions": [{"tool": "desktop_click", "idx": 2}, {"tool": "desktop_click", "idx": 0}]})
    check("a later idx means the control the model saw, renumbered or not", done == ["Three", "One"])
    done.clear()
    A._last = list(before)
    after = [{"name": "Gone", "type": "Button"}]
    out = T._execute_actions({"actions": [{"tool": "desktop_click", "idx": 2}, {"tool": "desktop_click", "idx": 0}]})
    check("a control that went away stops the batch", done == ["Three"] and "isn't there now" in out)
finally:
    A.snapshot, A._last, T._HANDLERS["desktop_click"], A.available = saved

# ------------------------------------------- a batch stays on the window it read
asked = []
saved = (A.snapshot, A.last_window, T._HANDLERS["desktop_click"])
A.snapshot, A.last_window = (lambda cap=50, window="": (asked.append(window), {})[1]), "Calculator"
T._HANDLERS["desktop_click"] = lambda p: "clicked"
try:
    T._execute_actions({"actions": [{"tool": "desktop_click", "name": "Three"}]})
    check("a name is looked up in the window the model read, not whatever's in front", asked == ["Calculator"])
finally:
    A.snapshot, A.last_window, T._HANDLERS["desktop_click"] = saved

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all key checks passed")
