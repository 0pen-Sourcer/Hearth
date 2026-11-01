"""The panel at the right edge while Inglen uses the desktop: what was asked, the
steps as they run, a question when one needs an answer, and Stop.

It slides in when the first desktop action of a turn starts and out when the
turn ends. It folds into the brand mark in the bottom-right corner and opens
again from it. Whichever surface started the turn (the window, the terminal, the
phone, a chat app) the turn runs through headless.run_once, which feeds it.

Pure ctypes on a per-pixel-alpha layered window (overlays.Surface), drawn with
numpy and GDI text, animated by critically damped springs on its own thread.
Out of every capture (overlays.register). Every public call is guarded and never
blocks, so it can never break a step.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from . import overlays

_WIN = sys.platform == "win32"

# Dark + Rose, as the site and the window paint it.
BG = overlays.hexrgb("#171615")
TOP = overlays.hexrgb("#0f0e0d")
BG0 = overlays.hexrgb("#121110")
HOVER = overlays.hexrgb("#211f1d")
BORDER = overlays.hexrgb("#2a2826")
BORDER_STRONG = overlays.hexrgb("#3b3835")
FG = overlays.hexrgb("#ecebe7")
FG_DIM = overlays.hexrgb("#a6a29b")
FG_FAINT = overlays.hexrgb("#6e6a64")
OK = overlays.hexrgb("#6fbf8e")
ERR = overlays.hexrgb("#e2695f")
ON_ACCENT = overlays.hexrgb("#16110e")

W = 300                 # panel width, px at 100%
DOT = 44                # the folded icon
MARGIN = 16             # from the work area's edge
MAX_ROWS = 6            # steps shown; earlier ones are counted
HOLD_DONE_S = 1.6       # Done stays this long before it slides out
YIELD_S = 1.6           # how long it lets an action through after one lands on it

_lock = threading.Lock()
_S: Dict[str, Any] = {
    "version": 0, "request": "", "steps": [], "ask": None, "phase": "off",
    "want": False, "stop": False, "collapsed": False, "done_at": 0.0, "seq": 0,
}
_answers: Dict[int, str] = {}
_panel = None
_failed = False


def _bump() -> None:
    _S["version"] += 1


def enabled() -> bool:
    if not _WIN or os.environ.get("HEARTH_CU_PANEL", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        from .desktop_session import _enabled
        return bool(_enabled())
    except Exception:
        return True


# ---------------------------------------------------------------- the turn's state

def begin_turn(request: str) -> None:
    """A turn started; nothing shows until it acts on the desktop."""
    try:
        with _lock:
            if _S["phase"] in ("work",):
                return
            _S.update(request=" ".join(str(request or "").split())[:600], steps=[], ask=None,
                      stop=False, phase="off" if not _S["want"] else _S["phase"])
            _bump()
    except Exception:
        pass


def step_start(name: str, doing: str, done: str = "") -> None:
    try:
        with _lock:
            for s in _S["steps"]:
                if s["state"] == "run" and s["name"] == name:
                    s["state"] = "ok"            # a step that never reported back is over by now
            _S["seq"] += 1
            _S["steps"].append({"id": _S["seq"], "name": str(name or ""), "doing": str(doing or name or ""),
                                "done": str(done or doing or name or ""), "state": "run"})
            del _S["steps"][:-60]
            _bump()
    except Exception:
        pass


def step_end(name: str, ok: bool = True) -> None:
    try:
        with _lock:
            for s in reversed(_S["steps"]):
                if s["state"] == "run" and s["name"] == name:
                    s["state"] = "ok" if ok else "err"
                    _bump()
                    break
    except Exception:
        pass


def show() -> None:
    """The turn acts on the desktop: slide in."""
    if not enabled():
        return
    try:
        with _lock:
            if _S["phase"] != "work":
                _S["phase"], _S["want"], _S["done_at"] = "work", True, 0.0
                _bump()
        _ensure()
    except Exception:
        pass


def finish(said: str = "Done") -> None:
    """The turn is over: Done for a moment, then out."""
    try:
        with _lock:
            if not _S["want"]:
                return
            for s in _S["steps"]:
                if s["state"] == "run":
                    s["state"] = "ok"
            _S["phase"], _S["done_at"] = "done", time.monotonic()
            if _S["ask"] is not None:
                _answers.setdefault(_S["ask"]["token"], "deny")
                _S["ask"] = None
            _bump()
    except Exception:
        pass


def ask(tool: str, args: Optional[Dict[str, Any]] = None, why: str = "", who: str = "") -> int:
    """Show a question with Allow and Deny, said as every surface says it
    (approval.py). Returns a token for answer()."""
    try:
        from .approval import card
        c = card(tool, args or {}, who=who, why=why)
        with _lock:
            _S["seq"] += 1
            tok = _S["seq"]
            _S["ask"] = {"token": tok, "tool": str(tool or ""), "head": c["head"], "what": c["what"],
                         "detail": c["detail"], "why": c["why"]}
            _S["collapsed_before_ask"] = _S["collapsed"]
            _S["collapsed"] = False             # a question needs to be seen
            _bump()
        return tok
    except Exception:
        return 0


def declined(what: str, aside: bool = False) -> None:
    """A step that was declined leaves one quiet line among the steps."""
    try:
        with _lock:
            _S["seq"] += 1
            _S["steps"].append({"id": _S["seq"], "name": "", "doing": "", "state": "declined",
                                "done": f"{'Not run' if aside else 'Declined'}: {what}"})
            _bump()
    except Exception:
        pass


def answer(token: int) -> Optional[str]:
    with _lock:
        return _answers.get(token)


def clear_ask(token: int) -> None:
    try:
        with _lock:
            a = _S["ask"]
            if a is not None and a["token"] == token:
                _S["ask"] = None
                if _S.pop("collapsed_before_ask", False):
                    _S["collapsed"] = True
                _bump()
            _answers.pop(token, None)
    except Exception:
        pass


def stop_requested() -> bool:
    return bool(_S["stop"])


def press(action: str) -> None:
    """A button on the panel: stop, allow, deny, collapse, expand."""
    with _lock:
        if action == "stop":
            if _S["phase"] == "work" and not _S["stop"]:
                _S["stop"] = True
                if _S["ask"] is not None:
                    _answers[_S["ask"]["token"]] = "deny"
        elif action in ("allow", "deny"):
            a = _S["ask"]
            if a is not None:
                _answers[a["token"]] = action
                _S["ask"] = None
                _S.pop("collapsed_before_ask", None)
        elif action == "collapse":
            _S["collapsed"] = True
        elif action == "expand":
            _S["collapsed"] = False
        _bump()


def state() -> Dict[str, Any]:
    with _lock:
        return {"request": _S["request"], "steps": [dict(s) for s in _S["steps"]],
                "ask": dict(_S["ask"]) if _S["ask"] else None, "phase": _S["phase"],
                "want": _S["want"], "stop": _S["stop"], "collapsed": _S["collapsed"]}


def make_way(x: int, y: int) -> None:
    p = _panel
    if p is not None:
        p.make_way(x, y)


def _ensure() -> None:
    global _panel, _failed
    if _failed or not _WIN:
        return
    if _panel is not None and _panel.is_alive():
        _panel.wake.set()
        return
    try:
        _panel = _Panel()
        _panel.start()
    except Exception:
        _failed = True


# ---------------------------------------------------------------- drawing

def _seg(np, X, Y, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    L = dx * dx + dy * dy or 1e-6
    t = np.clip(((X - x0) * dx + (Y - y0) * dy) / L, 0.0, 1.0)
    return np.sqrt((X - x0 - t * dx) ** 2 + (Y - y0 - t * dy) ** 2)


def _grid(np, h, w, ox=0.0, oy=0.0):
    Y = np.arange(h, dtype=np.float32)[:, None] + 0.5 + oy
    X = np.arange(w, dtype=np.float32)[None, :] + 0.5 + ox
    return X, Y


def _stroke(np, img, x, y, w, h, segs, half, rgb, alpha=1.0):
    """Line icon from segments in a box at (x, y)."""
    x, y, w, h = int(x), int(y), int(math.ceil(w)), int(math.ceil(h))
    H, Wd = img.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(Wd, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    X, Y = _grid(np, y1 - y0, x1 - x0, x0, y0)
    d = None
    for (ax, ay, bx, by) in segs:
        s = _seg(np, X, Y, ax, ay, bx, by)
        d = s if d is None else np.minimum(d, s)
    cov = np.clip(half + 0.5 - d, 0.0, 1.0)
    overlays.paint(np, img[y0:y1, x0:x1], cov, rgb, alpha)


def _mark(np, img, x, y, size, ink, dot_rgb, alpha=1.0):
    """The brand mark (assets/mark.svg, viewBox -2 -2 64 64) in a size x size box."""
    k = size / 64.0
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    n = int(math.ceil(size)) + 2
    H, Wd = img.shape[:2]
    if x0 < 0 or y0 < 0 or x0 + n > Wd or y0 + n > H:
        return
    X, Y = _grid(np, n, n, x0, y0)
    mx, my = (X - x) / k - 2.0, (Y - y) / k - 2.0
    d1 = np.sqrt((mx - 16.0) ** 2 + (my - np.clip(my, 22.0, 40.0)) ** 2)
    d2 = np.sqrt((mx - np.clip(mx, 22.0, 40.0)) ** 2 + (my - 16.0) ** 2)
    arc = np.abs(np.sqrt((mx - 22.0) ** 2 + (my - 22.0) ** 2) - 6.0)
    d3 = np.where((mx <= 22.0) & (my <= 22.0), arc, 1e9)
    d = np.minimum(np.minimum(d1, d2), d3)
    cov = np.clip((3.0 - d) * k + 0.5, 0.0, 1.0)
    overlays.paint(np, img[y0:y0 + n, x0:x0 + n], cov, ink, alpha)
    dd = (np.sqrt((mx - 38.0) ** 2 + (my - 38.0) ** 2) - 9.0) * k
    overlays.paint(np, img[y0:y0 + n, x0:x0 + n], np.clip(0.5 - dd, 0.0, 1.0), dot_rgb, alpha)


def _spinner(np, img, cx, cy, r, half, rgb, track, angle):
    """A ring with a quarter arc going round."""
    n = int(math.ceil(r + half + 2))
    x0, y0 = int(cx) - n, int(cy) - n
    H, Wd = img.shape[:2]
    if x0 < 0 or y0 < 0 or x0 + 2 * n > Wd or y0 + 2 * n > H:
        return
    X, Y = _grid(np, 2 * n, 2 * n, x0, y0)
    dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    ring = np.clip(half + 0.5 - np.abs(dist - r), 0.0, 1.0)
    sub = img[y0:y0 + 2 * n, x0:x0 + 2 * n]
    if track is not None:
        overlays.paint(np, sub, ring, track)
    a = (np.arctan2(Y - cy, X - cx) - angle) % (2 * math.pi)
    span = math.pi * 0.55
    soft = np.clip(np.minimum(a, span - a) * r + 0.5, 0.0, 1.0)
    overlays.paint(np, sub, ring * np.where(a <= span, soft, 0.0), rgb)


class _Panel(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True, name="hearth-cu-panel")
        self.wake = threading.Event()
        self.ok = False
        self.slide = overlays.Spring(1.0, 17.0)     # 1 = off screen to the right
        self.alpha = overlays.Spring(0.0, 20.0)
        self.morph = overlays.Spring(1.0, 19.0)     # 1 = open panel, 0 = the folded mark
        self.height = overlays.Spring(0.0, 22.0)
        self.dim = overlays.Spring(1.0, 26.0)       # drops while letting an action through
        self.yield_until = 0.0
        self.hover: Optional[str] = None
        self.down: Optional[str] = None
        self.hits: List[tuple] = []
        self.shape = (0, 0, 0, 0)                   # the card on screen, physical px
        self.origin = (0, 0)
        self.spin = 0.0
        self.scale = 0.0
        self.fonts: Dict[str, Any] = {}
        self.layout_key = None
        self.layout: Dict[str, Any] = {}
        self.np = None
        self.drawn_key = None
        self.framed_key = None
        self.cache = None
        self.spinners: List[tuple] = []
        self.card = (0, 0, 0, 0, 0)
        self.area = (0, 0, 1920, 1080, 1.0)

    # -- the window's messages
    def _proc(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x0021:                                       # WM_MOUSEACTIVATE
                return 3                                            # MA_NOACTIVATE
            if msg == 0x0084:                                       # WM_NCHITTEST
                return 1                                            # HTCLIENT (clear pixels pass through)
            if msg == 0x0020:                                       # WM_SETCURSOR
                hit = self._hit_at_cursor()
                cur = self.surface.u.LoadCursorW(None, 32649 if hit else 32512)    # IDC_HAND / IDC_ARROW
                self.surface.u.SetCursor(cur)
                return 1
            if msg in (0x0201, 0x0202):                             # WM_LBUTTONDOWN / UP
                x, y = lp & 0xFFFF, (lp >> 16) & 0xFFFF
                x, y = x - 0x10000 if x & 0x8000 else x, y - 0x10000 if y & 0x8000 else y
                hit = self._hit(x, y)
                if msg == 0x0201:
                    self.down = hit
                elif hit and hit == self.down:
                    self.down = None
                    press(hit)
                    self.wake.set()
                else:
                    self.down = None
                return 0
        except Exception:
            pass
        return self._defproc(hwnd, msg, wp, lp)

    def _hit(self, x, y):
        for (x0, y0, x1, y1, action) in self.hits:
            if x0 <= x < x1 and y0 <= y < y1:
                return action
        return None

    def _hit_at_cursor(self):
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        self.surface.u.GetCursorPos(ctypes.byref(pt))
        return self._hit(pt.x - self.origin[0], pt.y - self.origin[1])

    def make_way(self, x, y):
        sx0, sy0, sx1, sy1 = self.shape
        if sx0 <= x < sx1 and sy0 <= y < sy1:
            self.yield_until = time.monotonic() + YIELD_S
            try:
                self.surface.click_through(True)    # the action's click goes to what is under it
            except Exception:
                pass
            self.wake.set()

    # -- the thread
    def run(self):
        if not self._setup():
            return
        self._loop()

    def _setup(self) -> bool:
        try:
            import ctypes
            from ctypes import wintypes
            import numpy as np
            self.np = np
            overlays.per_monitor_thread()
            # LAYERED | TOPMOST | NOACTIVATE | TOOLWINDOW: clickable, never takes focus
            ex = 0x00080000 | 0x00000008 | 0x08000000 | 0x00000080
            u0 = ctypes.WinDLL("user32")
            u0.DefWindowProcW.restype = ctypes.c_ssize_t
            u0.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self._defproc = u0.DefWindowProcW              # messages arrive before the surface exists
            self.surface = overlays.Surface("HearthCUPanel", ex, self._proc)
            u = self.surface.u
            u.LoadCursorW.restype = wintypes.HANDLE
            u.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
            u.SetCursor.argtypes = [wintypes.HANDLE]
            u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            overlays.register(self.surface.hwnd, self.make_way)
            self.area = overlays.primary_work_area()
            self._fonts(self.area[4])
            self.ok = True
            return True
        except Exception:
            if os.environ.get("HEARTH_OVERLAY_DEBUG"):
                import traceback; traceback.print_exc()
            return False

    def _loop(self):
        last = time.monotonic()
        area_at = time.monotonic()
        top_at = 0.0
        while True:
            busy = False
            try:
                self.surface.pump()
                now = time.monotonic()
                dt, last = min(0.05, now - last), now
                if now - area_at > 2.0:
                    area_at = now
                    self.area = overlays.primary_work_area()
                    if self.area[4] != self.scale:
                        self._fonts(self.area[4])
                snap = self._snapshot(now)
                busy = self.tick(snap, now, dt)
                if now - top_at > 1.0:
                    top_at = now
                    self.surface.keep_on_top()
            except Exception:
                if os.environ.get("HEARTH_OVERLAY_DEBUG"):
                    import traceback; traceback.print_exc()
            self.wake.wait(1 / 60 if busy else 0.25)
            self.wake.clear()

    def _snapshot(self, now):
        with _lock:
            snap = {"version": _S["version"], "request": _S["request"],
                    "steps": [dict(s) for s in _S["steps"]],
                    "ask": dict(_S["ask"]) if _S["ask"] else None, "phase": _S["phase"],
                    "want": _S["want"], "stop": _S["stop"], "collapsed": _S["collapsed"],
                    "done_at": _S["done_at"]}
            if snap["phase"] == "done" and now - snap["done_at"] > HOLD_DONE_S:
                _S["want"], _S["phase"] = False, "off"
                snap["want"], snap["phase"] = False, "off"
                _bump()
        return snap

    def tick(self, snap, now, dt):
        """One frame: springs, then a full paint only when something drawn changed,
        else the spinners alone. Returns whether another frame is due soon."""
        busy = self._step(snap, now, dt)
        if not (self.surface.shown or snap["want"]):
            return busy
        hover = self._hit_at_cursor() if self.surface.shown else None
        if hover != self.hover:
            self.hover = hover
        key = (snap["version"], self.hover, self.scale, round(self.morph.x, 3), round(self.height.x, 1))
        if key != self.drawn_key:
            self._compose(snap)
            self.drawn_key = key
        self._frame()
        return busy

    def _fonts(self, scale):
        s = self.surface
        for f in self.fonts.values():
            try:
                s.g.DeleteObject(f)
            except Exception:
                pass
        px = lambda v: max(1, int(round(v * scale)))
        self.fonts = {"body": s.font(px(13)), "small": s.font(px(12.5)), "head": s.font(px(13), 600),
                      "title": s.font(px(13), 600), "mono": s.font(px(11.5), 400, "Consolas"),
                      "btn": s.font(px(12.5), 600)}
        self.scale = scale
        self.layout_key = None
        self.drawn_key = None

    def _step(self, snap, now, dt):
        want = snap["want"]
        self.slide.target = 0.0 if want else 1.0
        self.alpha.target = 1.0 if want else 0.0
        self.morph.target = 0.0 if snap["collapsed"] else 1.0
        yielding = now < self.yield_until
        self.dim.target = 0.16 if yielding else 1.0
        if not yielding and self.yield_until:
            self.yield_until = 0.0
            try:
                self.surface.click_through(False)
            except Exception:
                pass
        if self.layout_key is None or self.layout_key[0] != snap["version"]:
            self.layout = self._lay(snap)
            self.layout_key = (snap["version"], self.scale)
            if self.height.x <= 0:
                self.height.snap(self.layout["H"])
            self.height.target = self.layout["H"]
        for s in (self.slide, self.alpha, self.morph, self.height, self.dim):
            s.step(dt)
        spinning = snap["phase"] == "work"
        if spinning:
            self.spin = (self.spin + dt * 2 * math.pi * 0.85) % (2 * math.pi)
        moving = not all(s.settled(0.002) for s in (self.slide, self.alpha, self.morph, self.dim)) \
            or not self.height.settled(0.5)
        if not want and self.alpha.x < 0.01 and self.slide.x > 0.98:
            self.surface.hide()
            self.slide.snap(1.0)
            self.alpha.snap(0.0)
            self.morph.snap(self.morph.target)
            return False
        return moving or (spinning and want)

    def _px(self, v):
        return int(round(v * self.scale))

    def _lay(self, snap):
        """Positions for the open panel at its natural height (logical units x scale)."""
        s = self.surface
        f = self.fonts
        px = self._px
        Wp = px(W)
        pad = px(12)
        y = px(38) + pad                                        # under the header
        rows: List[tuple] = []
        # The request, right-aligned like a sent message, three lines at most.
        req = snap["request"] or ""
        bubble = None
        if req:
            maxw = Wp - 2 * pad - px(22)
            inner = maxw - px(22)
            flags = 0x10 | 0x800                                 # WORDBREAK | NOPREFIX
            line_h = s.measure("Ag", f["body"], inner, flags)[1]
            text = req
            tw, th = s.measure(text, f["body"], inner, flags)
            if th > 3 * line_h:
                lo, hi = 1, len(text)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if s.measure(text[:mid].rstrip() + "…", f["body"], inner, flags)[1] <= 3 * line_h:
                        lo = mid
                    else:
                        hi = mid - 1
                text = text[:lo].rstrip() + "…"
                tw, th = s.measure(text, f["body"], inner, flags)
            bw, bh = tw + px(22), th + px(16)
            bubble = (Wp - pad - bw, y, Wp - pad, y + bh, text)
            y += bh + px(12)
        steps = snap["steps"]
        hidden = max(0, len(steps) - MAX_ROWS)
        row_h = px(23)
        if hidden:
            rows.append(("more", f"{hidden} earlier step" + ("s" if hidden != 1 else ""), y))
            y += row_h
        for st in steps[hidden:]:
            rows.append(("step", st, y))
            y += row_h
        if steps:
            y += px(4)
        card = None
        a = snap["ask"]
        if a:
            cx0, cx1 = pad - px(2), Wp - pad + px(2)
            iw = cx1 - cx0 - px(20)
            cy = y + px(4)
            parts = []
            yy = cy + px(10)
            lh = s.measure("Ag", f["title"], iw, 0)[1]
            _, h1 = s.measure(a["head"], f["title"], iw, 0x10 | 0x800)
            h1 = min(h1, 2 * lh)
            parts.append(("head", a["head"], yy, h1))
            yy += h1 + px(8)
            if a["detail"]:
                lh = s.measure("Ag", f["mono"], iw, 0)[1]
                _, hm = s.measure(a["detail"], f["mono"], iw - px(16), 0x10 | 0x800 | 0x2000)
                hm = min(hm, 6 * lh)                   # four paths, "and N more", "to ..."
                parts.append(("detail", a["detail"], yy, hm + px(12)))
                yy += hm + px(12) + px(6)
            if a["why"]:
                lh = s.measure("Ag", f["small"], iw, 0)[1]
                _, hw = s.measure(a["why"], f["small"], iw, 0x10 | 0x800)
                hw = min(hw, 2 * lh)
                parts.append(("why", a["why"], yy, hw))
                yy += hw + px(6)
            yy += px(2)
            parts.append(("buttons", "", yy, px(30)))
            yy += px(30) + px(10)
            card = (cx0, cy, cx1, yy, parts)
            y = yy + px(8)
        foot_h = px(48)
        H = max(y + px(4), px(38) + px(60)) + foot_h
        H = min(H, int((self.area[3] - self.area[1]) * 0.7))
        return {"W": Wp, "H": H, "bubble": bubble, "rows": rows, "card": card, "foot_h": foot_h,
                "row_h": row_h, "pad": pad}

    def _compose(self, snap):
        """Paint the whole card at the springs' current values into self.cache."""
        np = self.np
        s = self.surface
        px = self._px
        L = self.layout
        Wc = L["W"] + px(MARGIN)
        Hc = int(max(L["H"], self.height.x)) + px(MARGIN)
        s.canvas(Wc, Hc)
        arr = s.array(np)
        accent = overlays.accent()
        accent2 = overlays.mix(accent, (255, 255, 255), 0.3)
        m = max(0.0, min(1.0, self.morph.x))
        dot = px(DOT)
        sw = dot + (L["W"] - dot) * m
        sh = dot + (self.height.x - dot) * m
        rad = dot / 2 + (px(12) - dot / 2) * m
        # bottom-right anchored, MARGIN of air outside it
        bx1, by1 = Wc - px(MARGIN), Hc - px(MARGIN)
        bx0, by0 = bx1 - sw, by1 - sh
        # Nothing is clipped while it folds: the words fade out in the first part of
        # the move, the card alone changes shape, and the mark fades in at the end.
        content = max(0.0, min(1.0, (m - 0.72) / 0.28))
        content = content * content * (3 - 2 * content)
        mark_a = max(0.0, min(1.0, (0.4 - m) / 0.4))
        mark_a = mark_a * mark_a * (3 - 2 * mark_a)
        arr[...] = 0
        self.hits = []
        self.spinners = []
        if content > 0.0:
            self._paint_panel(np, s, arr, snap, L, accent, accent2, bx0, by0, bx1, by1, sh)
            if content < 1.0:
                self.spinners = []
        # only the card's own box is worked on from here
        rx0, ry0 = max(0, int(bx0) - 1), max(0, int(by0) - 1)
        rx1, ry1 = min(Wc, int(math.ceil(bx1)) + 1), min(Hc, int(math.ceil(by1)) + 1)
        reg = arr[ry0:ry1, rx0:rx1]
        if content < 1.0:
            icon = np.zeros(reg.shape[:2] + (3,), dtype=np.float32)
            icon[...] = BG
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            ms = px(26)
            if mark_a > 0.0:
                _mark(np, icon, cx - ms / 2 - rx0, cy - ms / 2 - ry0, ms, FG, accent, mark_a)
            cur = reg[..., :3].astype(np.float32)[..., ::-1]
            mixed = cur * content + icon * (1 - content)
            reg[..., :3] = np.clip(mixed[..., ::-1] + 0.5, 0, 255).astype(np.uint8)
            if m < 0.5:
                self.hits = [(int(bx0), int(by0), int(bx1), int(by1), "expand")]
            if m < 0.02 and snap["phase"] == "work":          # the ring only once it has folded
                self.spinners.append((cx, cy, sw / 2 - px(3.5), px(1.0), accent, None))
        # the shape and its border, then premultiplied
        # Inside, the card is opaque and unchanged: only its rim (corners and edges) is worked out.
        h, w = reg.shape[:2]
        reg[..., 3] = 255
        e = int(math.ceil(rad)) + 3
        lx0, ty0 = bx0 - rx0, by0 - ry0
        lx1, ty1 = bx1 - rx0, by1 - ry0
        top, bot = min(h, int(ty0) + e), max(0, int(ty1) - e)
        bands = [(0, top, 0, w), (max(top, bot), h, 0, w)]
        if bot > top:
            bands += [(top, bot, 0, min(w, int(lx0) + 4)), (top, bot, max(0, int(lx1) - 4), w)]
        border = np.array(BORDER_STRONG[::-1], dtype=np.float32)
        for (y0, y1, x0, x1) in bands:
            if y1 <= y0 or x1 <= x0:
                continue
            sub = reg[y0:y1, x0:x1]
            hh, ww = sub.shape[:2]
            outer = overlays.rrect_cov(np, hh, ww, lx0 - x0, ty0 - y0, lx1 - x0, ty1 - y0, rad)
            inner = overlays.rrect_cov(np, hh, ww, lx0 + 1 - x0, ty0 + 1 - y0, lx1 - 1 - x0, ty1 - 1 - y0,
                                       max(0.0, rad - 1))
            ring = np.clip(outer - inner, 0.0, 1.0)[..., None]
            rgb = sub[..., :3].astype(np.float32) * (1 - ring) + border * ring
            sub[..., :3] = np.clip(rgb * outer[..., None] + 0.5, 0, 255).astype(np.uint8)
            sub[..., 3] = np.clip(outer * 255 + 0.5, 0, 255).astype(np.uint8)
        arr[:ry0] = 0
        arr[ry1:] = 0
        arr[:, :rx0] = 0
        arr[:, rx1:] = 0
        self.cache = arr.copy()
        self.card = (bx0, by0, bx1, by1, sw)

    def _frame(self):
        """Put the cached card on screen with the spinners at their angle."""
        np = self.np
        s = self.surface
        arr = s.array(np)
        if self.spinners:
            arr[...] = self.cache
            for (cx, cy, r, half, rgb, track) in self.spinners:
                n = int(math.ceil(r + half + 2))
                x0, y0 = int(cx) - n, int(cy) - n
                patch = arr[y0:y0 + 2 * n, x0:x0 + 2 * n]
                if x0 < 0 or y0 < 0 or patch.shape[:2] != (2 * n, 2 * n):
                    continue
                img = patch[..., :3][..., ::-1].astype(np.float32)
                tmp = np.zeros((2 * n, 2 * n, 3), dtype=np.float32)
                tmp[...] = img
                _spinner(np, tmp, cx - x0, cy - y0, r, half, rgb, track, self.spin)
                patch[..., :3] = np.clip(tmp[..., ::-1] + 0.5, 0, 255).astype(np.uint8)
        elif self.drawn_key != self.framed_key:
            arr[...] = self.cache
        self.framed_key = self.drawn_key
        left, top, right, bottom, _ = self.area
        Wc, Hc = s.size
        bx0, by0, bx1, by1, sw = self.card
        x = right - Wc + int(round(self.slide.x * (sw + self._px(MARGIN) * 2)))
        y = bottom - Hc
        self.origin = (x, y)
        self.shape = (int(x + bx0), int(y + by0), int(x + bx1), int(y + by1))
        s.present(x, y, self.alpha.x * self.dim.x)

    def _paint_panel(self, np, s, arr, snap, L, accent, accent2, bx0, by0, bx1, by1, sh):
        px = self._px
        f = self.fonts
        Hc, Wc = arr.shape[:2]
        Wp = L["W"]
        ox, oy = int(round(bx1)) - Wp, int(by1 - sh)  # the open card, right edge kept while it folds
        img = np.zeros((Hc, Wc, 3), dtype=np.float32)
        img[...] = BG
        head_h = px(38)
        foot_h = L["foot_h"]
        body_top, body_bot = oy + head_h, int(by1) - foot_h
        # body shapes
        bub = L["bubble"]
        if bub:
            x0, y0, x1, y1, _t = bub
            overlays.fill_rrect(np, img, ox + x0, oy + y0, ox + x1, oy + y1, px(12),
                                overlays.mix(BG, accent, 0.13), clip=(max(0, body_top), max(0, body_bot)))
        ic = px(13)
        for kind, st, ry in L["rows"]:
            cy = oy + ry + L["row_h"] / 2
            if cy - L["row_h"] / 2 < body_top or cy + L["row_h"] / 2 > body_bot:
                continue
            ix = ox + L["pad"] + 1
            if kind == "step" and st["state"] == "run":
                self.spinners.append((ix + ic / 2, cy, ic / 2 - px(1.5), px(0.75), accent2, BORDER_STRONG))
            elif kind == "step" and st["state"] == "declined":
                q = ic * 0.24
                _stroke(np, img, ix, cy - ic / 2, ic, ic,
                        [(ix + ic / 2 - q, cy - q, ix + ic / 2 + q, cy + q),
                         (ix + ic / 2 - q, cy + q, ix + ic / 2 + q, cy - q)], px(0.8), FG_FAINT)
        card = L["card"]
        if card:
            x0, y0, x1, y1, parts = card
            overlays.fill_rrect(np, img, ox + x0, oy + y0, ox + x1, oy + y1, px(10), overlays.mix(BORDER, accent, 0.45))
            overlays.fill_rrect(np, img, ox + x0 + 1, oy + y0 + 1, ox + x1 - 1, oy + y1 - 1, px(9),
                                overlays.mix(BG, accent, 0.07))
            for kind, _t, yy, hh in parts:
                if kind == "detail":
                    overlays.fill_rrect(np, img, ox + x0 + px(10), oy + yy, ox + x1 - px(10), oy + yy + hh, px(6), BG0)
                if kind == "buttons":
                    gap = px(6)
                    bw = (x1 - x0 - px(20) - gap) / 2
                    for i, act in enumerate(("allow", "deny")):
                        bx = ox + x0 + px(10) + i * (bw + gap)
                        by = oy + yy
                        hov = self.hover == act
                        if act == "allow":
                            overlays.fill_rrect(np, img, bx, by, bx + bw, by + hh, px(7), accent2 if hov else accent)
                        else:
                            overlays.fill_rrect(np, img, bx, by, bx + bw, by + hh, px(7), BORDER_STRONG)
                            overlays.fill_rrect(np, img, bx + 1, by + 1, bx + bw - 1, by + hh - 1, px(6),
                                                HOVER if hov else overlays.mix(BG, accent, 0.07))
                        self.hits.append((int(bx), int(by), int(bx + bw), int(by + hh), act))
        # header and footer bands
        img[max(0, oy):max(0, body_top)] = TOP
        img[max(0, body_bot):] = BG
        img[max(0, body_bot):max(0, body_bot) + 1] = BORDER
        ms = px(16)
        _mark(np, img, ox + px(12), oy + (head_h - ms) / 2, ms, FG, accent)
        # fold button: a short line, like a minimise
        fx = ox + Wp - px(12) - px(24)
        fy = oy + (head_h - px(24)) / 2
        if self.hover == "collapse":
            overlays.fill_rrect(np, img, fx, fy, fx + px(24), fy + px(24), px(6), HOVER)
        _stroke(np, img, fx, fy, px(24), px(24), [(fx + px(7.5), fy + px(12), fx + px(16.5), fy + px(12))],
                px(0.75), FG_DIM)
        self.hits.append((int(fx), int(fy), int(fx + px(24)), int(fy + px(24)), "collapse"))
        fcy = body_bot + foot_h / 2
        working = snap["phase"] == "work"
        if working:
            label = "Stopping" if snap["stop"] else "Stop"
            tw = s.measure(label, f["btn"], 400, 0x20)[0]
            bw, bh = tw + px(12) + px(10) + px(14) + px(8), px(28)
            bx1_ = ox + Wp - px(12)
            bx0_ = bx1_ - bw
            by0_ = fcy - bh / 2
            hov = self.hover == "stop" and not snap["stop"]
            overlays.fill_rrect(np, img, bx0_, by0_, bx1_, by0_ + bh, px(7), BORDER_STRONG)
            overlays.fill_rrect(np, img, bx0_ + 1, by0_ + 1, bx1_ - 1, by0_ + bh - 1, px(6), HOVER if hov else BG)
            sq = px(9)
            sx = bx0_ + px(12)
            overlays.fill_rrect(np, img, sx, fcy - sq / 2, sx + sq, fcy + sq / 2, px(2), FG_DIM if snap["stop"] else FG)
            stop_box = (bx0_, by0_, bx1_, by0_ + bh, sx + sq + px(8))
            if not snap["stop"]:
                self.hits.append((int(bx0_), int(by0_), int(bx1_), int(by0_ + bh), "stop"))
        else:
            ix = ox + px(12)
            ic2 = px(14)
            _stroke(np, img, ix, fcy - ic2 / 2, ic2, ic2,
                    [(ix + ic2 * 0.18, fcy + ic2 * 0.02, ix + ic2 * 0.42, fcy + ic2 * 0.26),
                     (ix + ic2 * 0.42, fcy + ic2 * 0.26, ix + ic2 * 0.84, fcy - ic2 * 0.24)], px(0.9), OK)
            stop_box = None
        # into the bitmap, then the words
        arr[..., :3] = np.clip(img[..., ::-1] + 0.5, 0, 255).astype(np.uint8)
        arr[..., 3] = 255
        T = s.text
        SL, VC, ELL, NP, WB, EC, RIGHT = 0x20, 0x4, 0x8000, 0x800, 0x10, 0x2000, 0x2
        T("Inglen", (ox + px(12) + ms + px(9), oy, ox + Wp - px(48), oy + head_h), f["head"], FG, SL | VC | NP)
        if bub:
            x0, y0, x1, y1, t = bub
            if oy + y0 >= body_top and oy + y1 <= body_bot:
                T(t, (ox + x0 + px(11), oy + y0 + px(8), ox + x1 - px(11), oy + y1 - px(8)), f["body"], FG, WB | NP)
        for kind, st, ry in L["rows"]:
            y0 = oy + ry
            if y0 < body_top or y0 + L["row_h"] > body_bot:
                continue
            tx = ox + L["pad"] + 1 + ic + px(9)
            if kind == "more":
                T(st, (tx, y0, ox + Wp - L["pad"], y0 + L["row_h"]), f["small"], FG_FAINT, SL | VC | ELL | NP)
                continue
            running = st["state"] == "run"
            words = st["doing"] if running else st["done"]
            col = {"run": FG, "err": ERR, "declined": FG_FAINT}.get(st["state"], FG_DIM)
            T(words, (tx, y0, ox + Wp - L["pad"], y0 + L["row_h"]), f["small"], col, SL | VC | ELL | NP)
        if card:
            x0, y0, x1, y1, parts = card
            ix0, ix1 = ox + x0 + px(10), ox + x1 - px(10)
            for kind, t, yy, hh in parts:
                if kind == "head":
                    T(t, (ix0, oy + yy, ix1, oy + yy + hh), f["title"], FG, WB | NP | EC | ELL)
                elif kind == "detail":
                    T(t, (ix0 + px(8), oy + yy + px(6), ix1 - px(8), oy + yy + hh), f["mono"], FG_DIM,
                      WB | NP | EC)                  # wraps as it was measured
                elif kind == "why":
                    T(t, (ix0, oy + yy, ix1, oy + yy + hh), f["small"], FG_DIM, WB | NP | EC | ELL)
                elif kind == "buttons":
                    gap = px(6)
                    bw = (x1 - x0 - px(20) - gap) / 2
                    T("Allow", (ix0, oy + yy, ix0 + bw, oy + yy + hh), f["btn"], ON_ACCENT, SL | VC | 0x1 | NP)
                    T("Deny", (ix0 + bw + gap, oy + yy, ix0 + 2 * bw + gap, oy + yy + hh), f["btn"], FG,
                      SL | VC | 0x1 | NP)
        if working:
            T("Inglen is using your PC" if not snap["stop"] else "Stopping after this step",
              (ox + px(12), body_bot, (stop_box[0] if stop_box else ox + Wp) - px(8), body_bot + L["foot_h"]),
              f["small"], FG_FAINT, SL | VC | ELL | NP)
            if stop_box:
                bx0_, by0_, bx1_, by1_, tx = stop_box
                T("Stopping" if snap["stop"] else "Stop", (tx, by0_, bx1_ - px(10), by1_), f["btn"],
                  FG_DIM if snap["stop"] else FG, SL | VC | NP)
        else:
            T("Done", (ox + px(12) + px(14) + px(8), body_bot, ox + Wp - px(12), body_bot + L["foot_h"]),
              f["small"], FG_DIM, SL | VC | NP)
