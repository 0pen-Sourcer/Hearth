"""Activity HUD — a small always-on-top pill that shows what JARVIS is doing on
the real desktop during computer-use.

When the agent drives the mouse/keyboard, the app it's controlling is focused,
not Hearth, so the chat window can't tell the user "I'm clicking Login" — they'd
just see the cursor move on its own. This floats a compact pill top-center with a
pulsing dot and a one-line status ("Clicking Sign in", "Typing…", "Reading the
screen", "Thinking…"). It never takes focus and is click-through, so it can't
intercept the very clicks JARVIS is making underneath it.

Raw win32 (pywin32), the SAME plumbing as voice_overlay / capture_overlay —
NOT tkinter, which is excluded from the packaged build and would silently no-op
in the shipped app. Windows-only, best-effort throughout: any failure disables
the HUD and is swallowed. show()/done()/hide() are safe to call from any thread.
"""
from __future__ import annotations

import os
import sys
import threading
import time

_disabled = False
_thread = None
_lock = threading.Lock()

# Shared render state, written by the public API from any thread, read by the
# render loop. text/kind/last are guarded together by _lock.
_state = {
    "text": "",
    "kind": "work",     # "work" | "done"
    "want": False,      # should the pill be visible
    "last": 0.0,        # ts of the most recent activity()
    "done_until": 0.0,  # hold the green tick until this ts, then fade
}

# Auto-hide the pill this many seconds after the last activity() call.
_IDLE_HIDE_S = 4.0

# Which tools count as "on the desktop" — callers use this to decide whether to
# surface the HUD at all. Kept here so tools.py and web.py agree on the set.
DESKTOP_TOOLS = {
    "computer_click", "computer_type", "computer_key", "computer_scroll",
    "computer_drag", "computer_move", "computer_screen",
    "desktop_click", "desktop_type", "desktop_snapshot",
    "smart_click", "focus_window", "screenshot", "view_image",
}

_VERB = {
    "computer_click": "Clicking",
    "desktop_click": "Clicking",
    "smart_click": "Clicking",
    "computer_type": "Typing",
    "desktop_type": "Typing",
    "computer_key": "Pressing",
    "computer_scroll": "Scrolling",
    "computer_drag": "Dragging",
    "computer_move": "Moving to",
    "focus_window": "Switching to",
    "computer_screen": "Looking at the screen",
    "screenshot": "Looking at the screen",
    "desktop_snapshot": "Reading the screen",
    "view_image": "Looking",
}

# Colors are win32 COLORREF (0x00BBGGRR).
_BG_KEY = 0x00010101      # near-black, painted transparent via color-key
_PILL = 0x00181114        # dark pill background (#141118)
_FG = 0x00ECECEC          # near-white text
_ACCENT = 0x006AA8C9      # gold dot (#c9a86a in BGR)
_DONE = 0x008FD87B        # green dot (#7bd88f in BGR)


def _enabled() -> bool:
    """HUD is on unless disabled, and only where a real desktop exists. Off for
    the phone/chat bridges and one-shot headless runs (JARVIS_NO_GUI=1)."""
    if sys.platform != "win32":
        return False
    v = os.environ.get("JARVIS_ACTIVITY_HUD")
    if v is not None:
        return v.strip().lower() not in ("0", "false", "no", "off")
    if os.environ.get("JARVIS_NO_GUI") == "1":
        return False
    try:
        from .web import _load_settings
        s = _load_settings()
        if "activity_hud" in s:
            return bool(s.get("activity_hud"))
    except Exception:
        pass
    return True


def label_for(name: str, args) -> str:
    """Build the one-line status for a desktop tool call."""
    verb = _VERB.get(name, "Working")
    hint = ""
    if isinstance(args, dict):
        for k in ("label", "target", "name", "window", "title", "key", "keys"):
            val = args.get(k)
            if isinstance(val, str) and val.strip():
                hint = val.strip()
                break
        if not hint and name in ("computer_type", "desktop_type"):
            txt = args.get("text")
            if isinstance(txt, str) and txt.strip():
                snip = txt.strip().replace("\n", " ")
                hint = (snip[:24] + "…") if len(snip) > 24 else snip
    if hint and verb.endswith(("ing", "to")):
        return f"{verb} {hint}"
    return verb


# ---------------------------------------------------------------------------
# public API (safe from any thread)
# ---------------------------------------------------------------------------
def activity(text: str) -> None:
    """Show/refresh the pill with a one-line status. No-op if disabled."""
    if _disabled or not _enabled():
        return
    with _lock:
        _state["text"] = str(text) or "Working"
        _state["kind"] = "work"
        _state["want"] = True
        _state["last"] = time.time()
        _state["done_until"] = 0.0
    _ensure_started()


def for_tool(name: str, args=None) -> None:
    """Convenience: surface the HUD for a desktop tool call by name."""
    if name in DESKTOP_TOOLS:
        activity(label_for(name, args))


def thinking() -> None:
    """Show a 'Thinking…' state — used between desktop actions so the gap while
    the model reasons doesn't read as the agent having stalled. Only refreshes an
    already-visible pill; it won't pop one on its own for a non-desktop turn."""
    if _disabled or not _enabled():
        return
    with _lock:
        if not _state["want"] or _state["kind"] == "done":
            return
        _state["text"] = "Thinking…"
        _state["last"] = time.time()


def done(text: str = "Done") -> None:
    """End a desktop task with a brief green tick, then fade. No-op if the pill
    isn't up (a turn with no desktop action stays silent)."""
    if _disabled or _thread is None:
        return
    with _lock:
        if not _state["want"]:
            return
        _state["text"] = str(text) or "Done"
        _state["kind"] = "done"
        _state["done_until"] = time.time() + 1.2


def hide() -> None:
    """Begin fading the pill out immediately."""
    if _disabled or _thread is None:
        return
    with _lock:
        _state["want"] = False


# ---------------------------------------------------------------------------
# win32 render thread
# ---------------------------------------------------------------------------
def _ensure_started() -> None:
    global _thread
    if _disabled:
        return
    if _thread is not None and _thread.is_alive():
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        try:
            _thread = threading.Thread(target=_run, name="hearth-activity-hud",
                                       daemon=True)
            _thread.start()
        except Exception:
            pass


def _run() -> None:
    global _disabled
    try:
        import ctypes
        import math
        from ctypes import wintypes
        import win32api
        import win32con
        import win32gui
    except Exception:
        _disabled = True
        return

    def _scale_color(cref: int, k: float) -> int:
        """Scale a COLORREF (0x00BBGGRR) brightness by k (0..1)."""
        k = max(0.0, min(1.0, k))
        b = (cref >> 16) & 0xFF
        g = (cref >> 8) & 0xFF
        r = cref & 0xFF
        return (int(b * k) << 16) | (int(g * k) << 8) | int(r * k)

    try:
        # DPI scale (physical px per logical) so the pill isn't tiny on a scaled
        # display. LOGPIXELSX = 88; a DPI-aware host reports the real DPI here.
        try:
            _dc0 = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(_dc0, 88) or 96
            ctypes.windll.user32.ReleaseDC(0, _dc0)
            sc = max(1.0, dpi / 96.0)
        except Exception:
            sc = 1.0

        def _px(n):
            return max(1, int(round(n * sc)))

        PAD_L, GAP, PAD_R = _px(15), _px(9), _px(17)
        HEIGHT = _px(38)
        DOT_BOX = _px(18)
        TXT_H = _px(15)
        Y = _px(24)

        hinst = win32api.GetModuleHandle(None)
        cls = "HearthActivityHUD"

        # Two fonts: text (Segoe UI) + dot glyph (Segoe UI Symbol).
        def _mkfont(h, face):
            try:
                lf = win32gui.LOGFONT()
                lf.lfHeight = h
                lf.lfFaceName = face
                lf.lfQuality = 5  # CLEARTYPE
                return win32gui.CreateFontIndirect(lf)
            except Exception:
                return None

        txt_font = _mkfont(-TXT_H, "Segoe UI")
        dot_font = _mkfont(_px(16), "Segoe UI Symbol")

        # A cached screen DC to measure text width (GetTextExtentPoint32W).
        class _SIZE(ctypes.Structure):
            _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

        _measure_dc = ctypes.windll.user32.GetDC(0)

        def _text_w(s: str) -> int:
            if not s:
                return 0
            try:
                old = ctypes.windll.gdi32.SelectObject(_measure_dc, txt_font)
                sz = _SIZE()
                ctypes.windll.gdi32.GetTextExtentPoint32W(
                    _measure_dc, s, len(s), ctypes.byref(sz))
                ctypes.windll.gdi32.SelectObject(_measure_dc, old)
                return int(sz.cx)
            except Exception:
                return int(len(s) * TXT_H * 0.6)

        # Render-frame snapshot, refreshed from _state each tick.
        frame = {"text": "", "kind": "work", "pulse": 0.0}

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_ERASEBKGND:
                return 1
            if msg == win32con.WM_PAINT:
                hdc, ps = win32gui.BeginPaint(hwnd)
                rect = win32gui.GetClientRect(hwnd)
                w, h = rect[2], rect[3]
                mem = win32gui.CreateCompatibleDC(hdc)
                bmp = win32gui.CreateCompatibleBitmap(hdc, w, h)
                old_bmp = win32gui.SelectObject(mem, bmp)
                # transparent key everywhere, then the pill body over it
                key = win32gui.CreateSolidBrush(_BG_KEY)
                win32gui.FillRect(mem, (0, 0, w, h), key)
                win32gui.DeleteObject(key)
                pill = win32gui.CreateSolidBrush(_PILL)
                win32gui.FillRect(mem, (0, 0, w, h), pill)
                win32gui.DeleteObject(pill)
                win32gui.SetBkMode(mem, win32con.TRANSPARENT)
                # pulsing dot
                if dot_font:
                    of = win32gui.SelectObject(mem, dot_font)
                    is_done = frame["kind"] == "done"
                    if is_done:
                        dot_col = _DONE
                    else:
                        k = 0.55 + 0.45 * math.sin(frame["pulse"])
                        dot_col = _scale_color(_ACCENT, k)
                    win32gui.SetTextColor(mem, dot_col)
                    win32gui.DrawText(mem, "●", -1,
                                      (PAD_L, 0, PAD_L + DOT_BOX, h),
                                      win32con.DT_CENTER | win32con.DT_VCENTER
                                      | win32con.DT_SINGLELINE)
                    win32gui.SelectObject(mem, of)
                # text
                if txt_font:
                    of = win32gui.SelectObject(mem, txt_font)
                    win32gui.SetTextColor(mem, _FG)
                    tx = PAD_L + DOT_BOX + GAP
                    win32gui.DrawText(mem, frame["text"], -1,
                                      (tx, 0, w - PAD_R, h),
                                      win32con.DT_LEFT | win32con.DT_VCENTER
                                      | win32con.DT_SINGLELINE
                                      | win32con.DT_END_ELLIPSIS)
                    win32gui.SelectObject(mem, of)
                win32gui.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, win32con.SRCCOPY)
                win32gui.SelectObject(mem, old_bmp)
                win32gui.DeleteObject(bmp)
                win32gui.DeleteDC(mem)
                win32gui.EndPaint(hwnd, ps)
                return 0
            if msg == win32con.WM_NCHITTEST:
                return win32con.HTTRANSPARENT   # click-through
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

        sw = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
              | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)
        hwnd = win32gui.CreateWindowEx(ex, cls, None, win32con.WS_POPUP,
                                       0, Y, 10, HEIGHT, 0, 0, hinst, None)
        win32gui.SetLayeredWindowAttributes(
            hwnd, _BG_KEY, 0, win32con.LWA_COLORKEY | win32con.LWA_ALPHA)

        MAX_A = 235
        alpha = 0.0
        cur_w = 0
        shown_win = False
        last_topmost = 0.0

        while True:
            with _lock:
                want = _state["want"]
                text = _state["text"]
                kind = _state["kind"]
                last = _state["last"]
                done_until = _state["done_until"]

            now = time.time()
            # Auto-hide: short hold after a Done tick, else the idle timeout.
            if want:
                if kind == "done":
                    if now > done_until:
                        want = False
                        with _lock:
                            _state["want"] = False
                elif (now - last) > _IDLE_HIDE_S:
                    want = False
                    with _lock:
                        _state["want"] = False

            frame["text"] = text
            frame["kind"] = kind

            # Resize + recenter when the text (width) changes.
            desired = PAD_L + DOT_BOX + GAP + _text_w(text) + PAD_R
            desired = max(_px(120), min(_px(560), desired))
            if want and desired != cur_w:
                cur_w = desired
                x = int((sw - cur_w) / 2)
                try:
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, Y,
                                          cur_w, HEIGHT,
                                          win32con.SWP_NOACTIVATE)
                except Exception:
                    pass

            # Ease alpha toward target.
            target = MAX_A if want else 0.0
            alpha += (target - alpha) * 0.30
            if want and not shown_win:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                except Exception:
                    pass
                shown_win = True
            try:
                win32gui.SetLayeredWindowAttributes(
                    hwnd, _BG_KEY, int(max(0, min(MAX_A, alpha))),
                    win32con.LWA_COLORKEY | win32con.LWA_ALPHA)
            except Exception:
                pass
            if not want and alpha < 2 and shown_win:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                except Exception:
                    pass
                shown_win = False
                cur_w = 0  # force a resize+recenter on next show

            # Advance the dot pulse (frozen on Done).
            if want and kind != "done":
                frame["pulse"] += 0.16

            try:
                if shown_win:
                    win32gui.InvalidateRect(hwnd, None, False)
                if now - last_topmost > 1.0:
                    last_topmost = now
                    if shown_win:
                        win32gui.SetWindowPos(
                            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                            win32con.SWP_NOSIZE | win32con.SWP_NOMOVE
                            | win32con.SWP_NOACTIVATE)
                win32gui.PumpWaitingMessages()
            except Exception:
                break
            time.sleep(0.033)   # ~30fps
    except Exception:
        _disabled = True
    finally:
        try:
            ctypes.windll.user32.ReleaseDC(0, _measure_dc)
        except Exception:
            pass
        for _f in (locals().get("txt_font"), locals().get("dot_font")):
            try:
                if _f:
                    win32gui.DeleteObject(_f)
            except Exception:
                pass
