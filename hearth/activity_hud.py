"""The activity pill: a small always-on-top label at the top of the screen that
says what Inglen is doing while it works the desktop ("Clicking Sign in",
"Typing", "Reading the screen", "Thinking...").

The app being driven has the focus, so the chat can't say it; this does. It never
takes focus, clicks pass through it, and it is left out of every capture
(overlays.register), so the model never reads its own label. Pure ctypes on a
per-pixel-alpha layered window, the same plumbing as the steps panel. Windows
only and best-effort: any failure disables it silently. Safe from any thread.
"""
from __future__ import annotations

from . import env as _env
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
    "execute_actions", "wait_for_element",
    "smart_click", "focus_window", "screenshot", "view_image",
    "open_app", "open_url", "open_in_browser", "manage_window", "arrange_windows",
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
    "open_app": "Opening",
    "open_url": "Opening",
    "open_in_browser": "Opening",
    "computer_screen": "Looking at the screen",
    "screenshot": "Looking at the screen",
    "desktop_snapshot": "Reading the screen",
    "view_image": "Looking",
}

_ACCENT = 0x006AA8C9      # COLORREF, when the look can't be read


def _accent() -> int:
    """The pulsing dot in the accent of the look in use, as a COLORREF."""
    try:
        from .voice_overlay import accent_colorref
        return accent_colorref()
    except Exception:
        return _ACCENT


def desktop_here() -> bool:
    """Whether the signs of desktop use can show here. A turn from the phone or a
    chat app acts on this same screen, so they show for those too."""
    if sys.platform != "win32":
        return False
    v = _env.get("ACTIVITY_HUD")
    return v is None or v.strip().lower() not in ("0", "false", "no", "off")


def _enabled() -> bool:
    """The pill is on unless turned off in Settings."""
    if not desktop_here():
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
# render thread
# ---------------------------------------------------------------------------
# Steady by construction: the window never moves or resizes while it is up. It is
# as wide as the longest label and centred once; the pill inside it changes width
# on a spring, holds its width a moment before shrinking (so "Thinking…" between
# two clicks doesn't make it breathe), and new words fade in where they stand.
PILL_BG = (0x14, 0x11, 0x18)
PILL_BORDER = (0x2A, 0x25, 0x30)
PILL_FG = (0xEC, 0xEC, 0xEC)
DONE_RGB = (0x6F, 0xBF, 0x8E)
MAX_W, MIN_W, HEIGHT, TOP = 520, 120, 32, 14       # px at 100%
PAD_L, DOT_D, GAP, PAD_R = 12, 7, 8, 15
SHRINK_AFTER_S = 1.2
FADE_IN_S = 0.18


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
            _thread = threading.Thread(target=_run, name="hearth-activity-hud", daemon=True)
            _thread.start()
        except Exception:
            pass


def _session_on() -> bool:
    try:
        from .desktop_session import is_active
        return bool(is_active())
    except Exception:
        return False


class _Pill:
    def __init__(self):
        import numpy as np
        from . import overlays as ov
        self.np, self.ov = np, ov
        ov.per_monitor_thread()
        # LAYERED | TRANSPARENT (click-through) | TOPMOST | NOACTIVATE | TOOLWINDOW
        ex = 0x00080000 | 0x00000020 | 0x00000008 | 0x08000000 | 0x00000080
        self.s = ov.Surface("HearthActivityHUD", ex)
        ov.register(self.s.hwnd)
        self.width = ov.Spring(MIN_W, 16.0)
        self.alpha = ov.Spring(0.0, 18.0)
        self.drop = ov.Spring(1.0, 18.0)            # 1 = tucked 6 px up, 0 = in place
        self.scale = 0.0
        self.area = None
        self.raw = None
        self.text = ""
        self.text_at = 0.0
        self.shorter_since = 0.0
        self.pulse0 = time.monotonic()
        self.dot_at = -10.0
        self.dot_rgb = (0xCF, 0x70, 0x89)
        self._layout()

    def _layout(self):
        ov = self.ov
        self.area = ov.primary_work_area()
        sc = self.area[4]
        if sc != self.scale:
            self.scale = sc
            self.font = self.s.font(round(13 * sc))
            self.s.canvas(self._px(MAX_W), self._px(HEIGHT + 8))

    def _px(self, v):
        return int(round(v * self.scale))

    def _text_w(self, t):
        return self.s.measure(t, self.font, 4000, 0x20 | 0x800)[0]

    def fit(self, t):
        """The label, cut with an ellipsis to the widest pill."""
        room = self._px(MAX_W - PAD_L - DOT_D - GAP - PAD_R)
        if self._text_w(t) <= room:
            return t
        lo, hi = 0, len(t)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._text_w(t[:mid].rstrip() + "…") <= room:
                lo = mid
            else:
                hi = mid - 1
        return t[:lo].rstrip() + "…"

    def frame(self, want, raw, kind, now, dt):
        if raw != self.raw:
            self.raw, self.text, self.text_at = raw, self.fit(raw), now
        natural = self._px(PAD_L + DOT_D + GAP + PAD_R) + self._text_w(self.text)
        natural = max(self._px(MIN_W), min(self._px(MAX_W), natural))
        if natural >= self.width.target or not self.alpha.x > 0.02:
            self.width.target, self.shorter_since = natural, 0.0
        elif not self.shorter_since:
            self.shorter_since = now
        elif now - self.shorter_since > SHRINK_AFTER_S:
            self.width.target, self.shorter_since = natural, 0.0
        if self.alpha.x < 0.02 and want:
            self.width.snap(natural)                     # appears at its size, not growing from nothing
        self.alpha.target = 1.0 if want else 0.0
        self.drop.target = 0.0 if want else 1.0
        for sp in (self.width, self.alpha, self.drop):
            sp.step(dt)
        if not want and self.alpha.x < 0.01:
            self.s.hide()
            return False
        self.paint(kind, now)
        return True

    def paint(self, kind, now):
        np, ov, s = self.np, self.ov, self.s
        Wc, Hc = s.size
        h = self._px(HEIGHT)
        w = self.width.x
        x0 = (Wc - w) / 2
        y0 = self._px(1)
        img = np.zeros((Hc, Wc, 3), dtype=np.float32)
        img[...] = PILL_BG
        outer = ov.rrect_cov(np, Hc, Wc, x0, y0, x0 + w, y0 + h, h / 2)
        inner = ov.rrect_cov(np, Hc, Wc, x0 + 1, y0 + 1, x0 + w - 1, y0 + h - 1, h / 2 - 1)
        ov.paint(np, img, np.clip(outer - inner, 0, 1), PILL_BORDER)
        cx, cy = x0 + self._px(PAD_L) + self._px(DOT_D) / 2, y0 + h / 2
        if kind == "done":
            dot, k = DONE_RGB, 1.0
        else:
            import math
            t = (now - self.pulse0) % 1.4 / 1.4
            k = 1.0 - 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * t))
            if now - self.dot_at > 1.0:                  # settings read once a second, not per frame
                c = _accent()
                self.dot_rgb, self.dot_at = (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF), now
            dot = self.dot_rgb
        ov.fill_circle(np, img, cx, cy, self._px(DOT_D) / 2, dot, k)
        arr = s.array(np)
        arr[..., :3] = np.clip(img[..., ::-1] + 0.5, 0, 255).astype(np.uint8)
        fade = min(1.0, (now - self.text_at) / FADE_IN_S)
        fade = fade * fade * (3 - 2 * fade)
        col = ov.mix(PILL_BG, PILL_FG, fade)
        tx = x0 + self._px(PAD_L + DOT_D + GAP)
        # Not clipped to the pill: the shape's alpha cuts it, so the words never re-ellipsize mid-spring.
        s.text(self.text, (tx, y0, tx + 4000, y0 + h), self.font, col, 0x20 | 0x4 | 0x800 | 0x100)
        a = outer
        arr[..., :3] = np.clip(arr[..., :3].astype(np.float32) * a[..., None] + 0.5, 0, 255).astype(np.uint8)
        arr[..., 3] = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
        left, top, right, bottom, _ = self.area
        x = left + ((right - left) - Wc) // 2
        y = top + self._px(TOP) - int(round(self._px(6) * self.drop.x))
        s.present(x, y, self.alpha.x)


def _run() -> None:
    global _disabled
    try:
        pill = _Pill()
    except Exception:
        if _env.get("OVERLAY_DEBUG"):
            import traceback; traceback.print_exc()
        _disabled = True
        return
    last = time.monotonic()
    area_at = last
    top_at = 0.0
    while True:
        try:
            pill.s.pump()
            now = time.monotonic()
            dt, last = min(0.05, now - last), now
            with _lock:
                want, text, kind = _state["want"], _state["text"], _state["kind"]
                last_act, done_until = _state["last"], _state["done_until"]
            wall = time.time()
            if want:
                if kind == "done":
                    if wall > done_until:
                        want = False
                elif wall - last_act > _IDLE_HIDE_S and not _session_on():
                    want = False
                if not want:
                    with _lock:
                        _state["want"] = False
            if now - area_at > 2.0:
                area_at = now
                pill._layout()
            shown = pill.frame(want, text, kind, now, dt)
            if shown and now - top_at > 1.0:
                top_at = now
                pill.s.keep_on_top()
            time.sleep(1 / 60 if shown else 0.1)
        except Exception:
            if _env.get("OVERLAY_DEBUG"):
                import traceback; traceback.print_exc()
            time.sleep(0.2)
