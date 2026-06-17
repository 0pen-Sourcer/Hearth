"""On-screen cue shown while Hearth is about to take a screenshot.

A topmost, click-through, layered window showing a few purple dots that count
down, so the user can SEE the shutter is coming during the screenshot delay —
instead of wondering whether anything is happening. No text; just the dots.

Windows-only (raw win32 via pywin32 — tkinter is excluded from the packaged
build). Everything is best-effort and swallowed: the cue can NEVER block or break
the actual capture. The caller shows it for the delay window and it auto-removes
before the grab, so it does not appear in the screenshot.

This is also the seed of a general Hearth overlay (HUD / watchers) — keep the
window plumbing reusable.
"""
from __future__ import annotations

import sys
import threading
import time
from math import ceil as _ceil

_PURPLE = 0x00E060A0  # win32 COLORREF is 0x00BBGGRR — this is a violet (a0,60,e0)
_DIM = 0x00603040     # spent-dot color (dim violet)
_BG_KEY = 0x00010101  # near-black color-key painted transparent
_MAX_DOTS = 5         # hard cap so a huge delay can't carpet the screen with dots


def flash(duration: float = 0.8) -> None:
    """Show the capture cue for ~duration seconds, then auto-remove. Non-blocking
    (daemon thread). No-op off Windows or if win32 isn't importable."""
    if sys.platform != "win32":
        return
    try:
        threading.Thread(target=_run, args=(max(0.3, float(duration)),),
                         daemon=True).start()
    except Exception:
        pass


def _run(duration: float) -> None:
    try:
        import win32api
        import win32con
        import win32gui
    except Exception:
        return
    try:
        hinst = win32api.GetModuleHandle(None)
        cls = "HearthCaptureCue"

        start = time.time()
        deadline = start + duration
        total_dots = max(1, min(_MAX_DOTS, _ceil(duration)))
        _font = [None]  # created once below; the paint handler reuses it

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_PAINT:
                hdc, ps = win32gui.BeginPaint(hwnd)
                rect = win32gui.GetClientRect(hwnd)
                bg = win32gui.CreateSolidBrush(_BG_KEY)
                win32gui.FillRect(hdc, rect, bg)
                win32gui.DeleteObject(bg)
                # Dots are a countdown: `lit` = dots still remaining, capped.
                remaining = max(0.0, deadline - time.time())
                lit = max(0, min(total_dots, _ceil(remaining)))
                # Render the dots as a font glyph so they're ClearType-smooth
                # (GDI Ellipse has no anti-aliasing — that's the "torn pixels").
                # Font is created once (in _run) and reused. Lit dots bright
                # violet, spent dots dim.
                old_font = win32gui.SelectObject(hdc, _font[0]) if _font[0] else None
                win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
                x = 12
                for i in range(total_dots):
                    win32gui.SetTextColor(hdc, _PURPLE if i < lit else _DIM)
                    win32gui.DrawText(hdc, "●", -1,
                                      (x, 0, x + 22, rect[3]),
                                      win32con.DT_CENTER | win32con.DT_VCENTER
                                      | win32con.DT_SINGLELINE)
                    x += 22
                if old_font:
                    win32gui.SelectObject(hdc, old_font)
                win32gui.EndPaint(hwnd, ps)
                return 0
            if msg == win32con.WM_DESTROY:
                win32gui.PostQuitMessage(0)
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = cls
        wc.hInstance = hinst
        wc.lpfnWndProc = _wnd_proc
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass  # already registered from a previous flash

        # Create the dot font once (CreateFontIndirect — win32gui has no
        # CreateFont). ClearType quality for smooth, non-jagged dots.
        try:
            lf = win32gui.LOGFONT()
            lf.lfHeight = 20
            lf.lfFaceName = "Segoe UI Symbol"
            lf.lfQuality = 5  # CLEARTYPE_QUALITY
            _font[0] = win32gui.CreateFontIndirect(lf)
        except Exception:
            _font[0] = None

        sw = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        width, height = total_dots * 22 + 24, 38
        x = sw - width - 24   # top-RIGHT corner
        y = 24
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
              | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW
              | win32con.WS_EX_NOACTIVATE)
        hwnd = win32gui.CreateWindowEx(ex, cls, None, win32con.WS_POPUP,
                                       x, y, width, height, 0, 0, hinst, None)
        win32gui.SetLayeredWindowAttributes(
            hwnd, _BG_KEY, 0, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

        # Animate: fade + slide-down on entry, fade-out on exit (no abrupt pop).
        # The fade-out fully completes (alpha 0) BEFORE the window is destroyed
        # and well before the caller grabs the screen, so the dots are never in
        # the shot.
        _FADE = 0.22
        _MAX_A = 235
        while True:
            el = time.time() - start
            if el >= duration:
                break
            if el < _FADE:                       # pop-in
                k = el / _FADE
            elif el > duration - _FADE:           # fade-out
                k = max(0.0, (duration - el) / _FADE)
            else:
                k = 1.0
            alpha = int(_MAX_A * k)
            yy = y - int((1.0 - min(1.0, el / _FADE)) * 8)  # slide down ~8px
            try:
                win32gui.SetLayeredWindowAttributes(
                    hwnd, _BG_KEY, alpha,
                    win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, yy, 0, 0,
                                      win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
                win32gui.InvalidateRect(hwnd, None, True)
            except Exception:
                break
            win32gui.PumpWaitingMessages()
            time.sleep(0.02)
        try:
            win32gui.DestroyWindow(hwnd)
            win32gui.PumpWaitingMessages()
        except Exception:
            pass
        try:
            if _font[0]:
                win32gui.DeleteObject(_font[0])
        except Exception:
            pass
    except Exception:
        return
