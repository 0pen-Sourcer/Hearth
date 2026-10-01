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
from math import hypot


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


# The accent of each look, as the app paints it. Classic is violet whatever
# accent is picked; the others use the picked accent.
_ACCENTS = {"ember": (0xD9, 0x77, 0x4A), "sage": (0x74, 0xA8, 0x83), "ocean": (0x5B, 0x93, 0xC2),
            "rose": (0xCF, 0x70, 0x89), "violet": (0x8B, 0x6C, 0xF0), "mono": (0xA3, 0xA0, 0x9A)}
_CLASSIC = (0x8B, 0x5C, 0xF6)


def accent_rgb(settings: dict) -> tuple:
    """The dot colour for the saved look."""
    if (settings.get("ui_theme") or "classic") == "classic":
        return _CLASSIC
    return _ACCENTS.get(settings.get("ui_accent") or "rose", _ACCENTS["rose"])


def _saved_accent() -> tuple:
    try:
        from .web import _load_settings
        return accent_rgb(_load_settings())
    except Exception:
        return _CLASSIC


def _dot(intensity: float, rgb: tuple) -> int:
    """The accent as a COLORREF (0x00BBGGRR), scaled by intensity 0..1:
    brighter dots read as energy, dim ones as the resting grid."""
    k = max(0.12, min(1.0, intensity))
    r, g, b = int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k)
    return (b << 16) | (g << 8) | r


def grid_level(state: str, tts_level: float, mic_level: float) -> float:
    """How far the bloom reaches, 0..1, by the same rules as the grid in the
    window: Hearth's voice while it speaks, your mic while it listens, and
    nothing moving when nothing is being said."""
    if tts_level > 0.02:
        return min(1.0, max(0.35, 0.35 + tts_level * 0.75))
    if state == "listening" and mic_level > 0.03:
        return min(1.0, mic_level * 0.85)
    return 0.0


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
        # level: the bloom's reach. ring: the radius of the thinking ring, or
        # None. rgb: the look's accent, re-read now and then.
        state = {"level": 0.0, "ring": None, "rgb": _saved_accent()}
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
                ring = state["ring"]
                for r in range(_ROWS):
                    for c in range(_COLS):
                        dist = hypot(c - cx, r - cy)
                        if ring is not None:
                            inten = 0.6 if abs(dist - ring) < 0.85 else 0.0
                        else:
                            inten = max(0.0, min(1.0, (reach - dist) / 1.6))
                        if dist < 0.7:
                            inten = max(inten, 0.5)     # the centre stays lit, as in the window
                        inten = max(0.14, inten)        # resting grid always faintly visible
                        win32gui.SetTextColor(mem, _dot(inten, state["rgb"]))
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
        _last_check = 0.0
        while not _stop.is_set():
            # Once a second: follow the look, and leave when voice has ended.
            # The HUD belongs to a voice session; whoever started it may never
            # get to call stop() (a window closed mid-reply, a dropped stream).
            _now = time.time()
            if _now - _last_check > 1.0:
                _last_check = _now
                state["rgb"] = _saved_accent()
                try:
                    from . import realtime_voice as _rtv
                    if _rtv._session is None and _now - start_t > 3.0:
                        break
                except Exception:
                    pass
            # Focus-based handoff: when the Hearth GUI window is foreground it
            # draws its OWN in-app grid, so hide this desktop HUD to avoid two
            # grids. Show it only when focus is elsewhere (a game / other app) —
            # the whole point is a visible listening/talking cue when no Hearth
            # window is in view.
            try:
                fg = win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
            except Exception:
                fg = ""
            # Hide over any Hearth window to avoid a double grid. Match the title
            # AND the localhost URL, because when the native window fails and the
            # UI opens in a browser, the tab title is the address, not "Hearth" —
            # that mismatch is what left both grids on screen (the reported dupe).
            _fgl = fg.lower()
            if ("hearth" in _fgl or "127.0.0.1:8765" in _fgl
                    or "localhost:8765" in _fgl):
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
            # The same motion as the grid in the window, so the two never
            # disagree: thinking is one slow ring, everything else a bloom.
            try:
                from . import voice as _v
                lvl = _v.current_level()
                mic = _v.current_mic_level()
                st = _v.voice_state()
            except Exception:
                lvl, mic, st = 0.0, 0.0, "idle"
            t = time.time() - start_t
            if st == "thinking" and lvl <= 0.02:
                ph = (t % 3.2) / 3.2
                state["ring"] = ph * (hypot((_COLS - 1) / 2.0, (_ROWS - 1) / 2.0) + 1) if ph < 0.9 else -9.0
                state["level"] = 0.0
            else:
                state["ring"] = None
                target = grid_level(st, lvl, mic)
                # Gentle attack, slower release: raw amplitude is spiky.
                state["level"] += (target - state["level"]) * (0.22 if target > state["level"] else 0.08)
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
