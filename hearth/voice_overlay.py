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

import os
import sys
import threading
import time
from math import hypot, sin


def _corner_xy(sw: int, sh: int, width: int, height: int):
    """HUD position from HEARTH_HUD_CORNER (tr/tl/br/bl), default top-right."""
    m = 28
    corner = (os.environ.get("HEARTH_HUD_CORNER", "tr") or "tr").strip().lower()
    right = sw - width - m
    bottom = sh - height - m
    return {
        "tr": (right, m), "tl": (m, m),
        "br": (right, bottom), "bl": (m, bottom),
    }.get(corner, (right, m))

_BG_KEY = 0x00010101   # near-black, painted transparent via color-key
# Match the in-app voice grid (15x15). A wide 11x6 rectangle read as a different
# component entirely next to the app's square one, and a radial pulse on a grid
# that much wider than it is tall never looks circular.
_COLS, _ROWS = 15, 15  # grid size
_SP = 12               # px spacing between dots, tighter so the HUD stays small
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
            if msg == win32con.WM_ERASEBKGND:
                # Claim the erase. Letting Windows clear the window before every
                # WM_PAINT is half of the shimmer: the grid blanks then redraws
                # 60 times a second. WM_PAINT repaints every pixel anyway.
                return 1
            if msg == win32con.WM_PAINT:
                hdc, ps = win32gui.BeginPaint(hwnd)
                rect = win32gui.GetClientRect(hwnd)
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                # Draw off-screen and blit once. Drawing 66 dots straight to the
                # window DC means the partially-drawn grid is briefly on screen
                # every frame, which is the rest of the shimmer.
                mem = win32gui.CreateCompatibleDC(hdc)
                bmp = win32gui.CreateCompatibleBitmap(hdc, w, h)
                old_bmp = win32gui.SelectObject(mem, bmp)
                bg = win32gui.CreateSolidBrush(_BG_KEY)
                win32gui.FillRect(mem, (0, 0, w, h), bg)
                win32gui.DeleteObject(bg)
                old = win32gui.SelectObject(mem, font[0]) if font[0] else None
                win32gui.SetBkMode(mem, win32con.TRANSPARENT)
                cx, cy = (_COLS - 1) / 2.0, (_ROWS - 1) / 2.0
                maxd = hypot(cx, cy)
                reach = state["level"] * (maxd + 0.8)
                for r in range(_ROWS):
                    for c in range(_COLS):
                        dist = hypot(c - cx, r - cy)
                        inten = max(0.0, min(1.0, (reach - dist) / 1.6))
                        inten = max(0.14, inten)        # resting grid always faintly visible
                        win32gui.SetTextColor(mem, _violet(inten))
                        x = _PAD + c * _SP
                        y = _PAD + r * _SP
                        win32gui.DrawText(mem, "●", -1, (x, y, x + _SP, y + _SP),
                                          win32con.DT_CENTER | win32con.DT_VCENTER
                                          | win32con.DT_SINGLELINE)
                win32gui.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, win32con.SRCCOPY)
                if old:
                    win32gui.SelectObject(mem, old)
                win32gui.SelectObject(mem, old_bmp)
                win32gui.DeleteObject(bmp)
                win32gui.DeleteDC(mem)
                win32gui.EndPaint(hwnd, ps)
                return 0
            if msg == win32con.WM_NCHITTEST:
                # Ctrl held = draggable (HTCAPTION); else click-through.
                if win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000:
                    return win32con.HTCAPTION
                return win32con.HTTRANSPARENT
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
        x, y = _corner_xy(sw, sh, width, height)   # top-right default; env-configurable
        # Click-through is done via WM_NCHITTEST (HTTRANSPARENT) NOT
        # WS_EX_TRANSPARENT — that way holding Ctrl can flip it to HTCAPTION and
        # the whole window becomes draggable, while a normal click still passes
        # through to the game underneath.
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
              | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)
        hwnd = win32gui.CreateWindowEx(ex, cls, None, win32con.WS_POPUP,
                                       x, y, width, height, 0, 0, hinst, None)
        win32gui.SetLayeredWindowAttributes(
            hwnd, _BG_KEY, 230, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

        _shown = True
        _last_topmost = 0.0
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
            # Hide over any Hearth-titled window (avoid a double grid).
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
            # Motion per state: speaking=amplitude-reactive, thinking=pulse,
            # listening=steady hold, idle=slow breathe.
            try:
                from . import voice as _v
                lvl = _v.current_level()
                st = _v.voice_state()
            except Exception:
                lvl, st = 0.0, "idle"
            t = time.time() - start_t
            if lvl > 0.02:
                target = min(1.0, max(0.35, 0.35 + lvl * 0.75))
            elif st == "thinking":
                target = 0.42 + 0.16 * (0.5 + 0.5 * sin(t * 1.9))   # slow, shallow breathe
            elif st == "listening":
                target = 0.58 + 0.05 * sin(t * 2.6)                 # steady hold
            else:
                target = 0.30 + 0.06 * sin(t * 2.0)                 # calm breathe
            # Ease toward target (~0.22/frame @60fps) for smooth motion.
            state["level"] += (target - state["level"]) * 0.22
            try:
                # bErase=False: WM_PAINT repaints every pixel from the back
                # buffer, so asking Windows to blank it first only adds flicker.
                win32gui.InvalidateRect(hwnd, None, False)
                # Re-assert topmost about once a second, not every frame. At
                # 60fps this was constant compositor churn for no benefit, and
                # it fought the window manager while dragging.
                _now = time.time()
                if _now - _last_topmost > 1.0:
                    _last_topmost = _now
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                          win32con.SWP_NOSIZE | win32con.SWP_NOMOVE
                                          | win32con.SWP_NOACTIVATE)
                win32gui.PumpWaitingMessages()
            except Exception:
                break
            time.sleep(0.033)   # ~30fps, plenty for a breathing grid

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
