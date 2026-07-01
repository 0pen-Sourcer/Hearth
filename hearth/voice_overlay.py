"""Voice-mode HUD overlay — a small, topmost, click-through dot grid that pulses
center-out while Hearth is in voice mode, so you can SEE it's listening / talking
even when no window is focused.

Same win32 plumbing as capture_overlay (topmost, layered, color-keyed,
click-through, no-activate), but PERSISTENT + animated: it's the screenshot cue's
window mechanics married to the GUI voice mode's dot grid, minified onto the
desktop. start() shows it; stop() removes it. Windows-only, best-effort, never
blocks the voice loop.
"""
from __future__ import annotations

import sys
import threading
import time
from math import hypot, sin

_BG_KEY = 0x00010101   # near-black, painted transparent via color-key
_COLS, _ROWS = 11, 6   # grid size
_SP = 17               # px spacing between dots
_PAD = 16

_stop = threading.Event()
_thread = None


def _violet(intensity: float) -> int:
    """Hearth violet COLORREF (0x00BBGGRR) scaled by intensity 0..1 — brighter
    dots read as 'energy', dim ones as the resting grid. Matches the GUI voice
    grid's purple (≈ #9D7BFF); the old 0xE0/0x60/0xC0 was magenta/pink."""
    k = max(0.12, min(1.0, intensity))
    r, g, b = int(0x9D * k), int(0x7B * k), int(0xFF * k)
    return (b << 16) | (g << 8) | r


def start() -> None:
    """Show the voice HUD (non-blocking). No-op off Windows / if win32 missing,
    or if already showing."""
    global _thread
    if sys.platform != "win32":
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    try:
        _thread = threading.Thread(target=_run, daemon=True, name="hearth-voice-hud")
        _thread.start()
    except Exception:
        pass


def stop() -> None:
    _stop.set()


def _run() -> None:
    try:
        import win32api
        import win32con
        import win32gui
    except Exception:
        return
    try:
        hinst = win32api.GetModuleHandle(None)
        cls = "HearthVoiceHUD"
        start_t = time.time()
        state = {"level": 0.3}          # shared pulse level, updated each frame
        font = [None]

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_PAINT:
                hdc, ps = win32gui.BeginPaint(hwnd)
                rect = win32gui.GetClientRect(hwnd)
                bg = win32gui.CreateSolidBrush(_BG_KEY)
                win32gui.FillRect(hdc, rect, bg)
                win32gui.DeleteObject(bg)
                old = win32gui.SelectObject(hdc, font[0]) if font[0] else None
                win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
                cx, cy = (_COLS - 1) / 2.0, (_ROWS - 1) / 2.0
                maxd = hypot(cx, cy)
                reach = state["level"] * (maxd + 0.8)
                for r in range(_ROWS):
                    for c in range(_COLS):
                        dist = hypot(c - cx, r - cy)
                        inten = max(0.0, min(1.0, (reach - dist) / 1.6))
                        inten = max(0.14, inten)        # resting grid always faintly visible
                        win32gui.SetTextColor(hdc, _violet(inten))
                        x = _PAD + c * _SP
                        y = _PAD + r * _SP
                        win32gui.DrawText(hdc, "●", -1, (x, y, x + _SP, y + _SP),
                                          win32con.DT_CENTER | win32con.DT_VCENTER
                                          | win32con.DT_SINGLELINE)
                if old:
                    win32gui.SelectObject(hdc, old)
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
            pass

        try:
            lf = win32gui.LOGFONT()
            lf.lfHeight = 11
            lf.lfFaceName = "Segoe UI Symbol"
            lf.lfQuality = 5  # CLEARTYPE
            font[0] = win32gui.CreateFontIndirect(lf)
        except Exception:
            font[0] = None

        sw = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        sh = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        width = _COLS * _SP + _PAD
        height = _ROWS * _SP + _PAD
        x = (sw - width) // 2          # bottom-center HUD
        y = sh - height - 70
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
              | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW
              | win32con.WS_EX_NOACTIVATE)
        hwnd = win32gui.CreateWindowEx(ex, cls, None, win32con.WS_POPUP,
                                       x, y, width, height, 0, 0, hinst, None)
        win32gui.SetLayeredWindowAttributes(
            hwnd, _BG_KEY, 230, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

        _shown = True
        while not _stop.is_set():
            # Focus-based handoff: when the Hearth GUI window is foreground it
            # draws its OWN in-app grid, so hide this desktop HUD to avoid two
            # grids. Show it only when focus is elsewhere (a game / other app) —
            # the whole point is a visible listening/talking cue when no Hearth
            # window is in view.
            try:
                fg = win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
            except Exception:
                fg = ""
            # Substring (not ==) so any Hearth-titled window hides the HUD —
            # erring toward hiding, since a double grid is the worse outcome.
            if "hearth" in fg.lower():
                if _shown:
                    try:
                        win32gui.SetLayeredWindowAttributes(hwnd, _BG_KEY, 0,
                            win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
                    except Exception:
                        pass
                    _shown = False
                win32gui.PumpWaitingMessages()
                time.sleep(0.08)
                continue
            if not _shown:
                try:
                    win32gui.SetLayeredWindowAttributes(hwnd, _BG_KEY, 230,
                        win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
                except Exception:
                    pass
                _shown = True
            # Reach is driven by Hearth's REAL TTS amplitude (voice.current_level),
            # so the grid reacts to its own speech — quiet = center, loud = edge —
            # not a canned loop. A slow idle breathe when silent keeps it alive.
            try:
                from . import voice as _v
                lvl = _v.current_level()
            except Exception:
                lvl = 0.0
            t = time.time() - start_t
            idle = 0.30 + 0.06 * sin(t * 2.0)
            state["level"] = min(1.0, max(idle, 0.35 + lvl * 0.75)) if lvl > 0.02 else idle
            try:
                win32gui.InvalidateRect(hwnd, None, True)
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, 0, 0,
                                      win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
                win32gui.PumpWaitingMessages()
            except Exception:
                break
            time.sleep(0.045)

        try:
            win32gui.DestroyWindow(hwnd)
            win32gui.PumpWaitingMessages()
        except Exception:
            pass
        try:
            if font[0]:
                win32gui.DeleteObject(font[0])
        except Exception:
            pass
    except Exception:
        return
