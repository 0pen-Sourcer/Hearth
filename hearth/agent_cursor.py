"""The pointer Hearth draws while it controls the desktop, so its actions always
read apart from the user's own mouse.

It is the brand mark (assets/mark.svg): the rounded corner is the pointer, its tip
just off the point being acted on, and the dot shows the button. While gliding it
turns to face where it is going; a click fills the dot with the accent, a double
click fills it twice, a drag holds it filled.

Pure ctypes: a per-monitor DPI-aware layered window, drawn by hand into a
premultiplied ARGB bitmap, animated by critically damped springs on its own
thread. Best-effort and fully guarded: if anything fails it silently no-ops, so it
can NEVER break an actual click. Windows-only for now (so is the desktop-control
layer); elsewhere these calls are no-ops. Toggle off with HEARTH_AGENT_CURSOR=0.
"""
from __future__ import annotations

import os
import sys
import math
import time
import ctypes
import threading
from ctypes import wintypes

_WIN = sys.platform == "win32"
_ENABLED = os.environ.get("HEARTH_AGENT_CURSOR", "1") not in ("0", "false", "no")


def _accent() -> tuple:
    try:
        from .voice_overlay import _saved_accent
        return _saved_accent()
    except Exception:
        return (0xE0, 0x7B, 0x4C)


# Geometry in the units of assets/mark.svg.
_ARC_C, _ARC_R = 22.0, 6.0          # the corner's arc
_HALF = 3.0                         # half the stroke width
_LEG_END = 40.0                     # both legs run from the arc to 40
_DOT_C, _DOT_R = 38.0, 9.0
_RING = 1.5                         # half-width of the dot's ring when it is empty
_TIP = _ARC_C - (_ARC_R + _HALF) / math.sqrt(2)   # outer point of the corner

_CREAM = (0xEC, 0xEB, 0xE7)         # Dark's --fg
_INK = (0x1F, 0x1C, 0x19)           # edge, so the cream reads on light content
_INK_A = 0.62

_UNIT = 0.7                         # px per mark unit at 100%
_GAP = 2.5                          # px from the click point to the mark, so the point stays clear
_EDGE = 1.2                         # px of ink around the mark
_REST = -135.0                      # heading at rest: up and to the left, as the mark is drawn
_SWELL = 0.1                        # the dot grows this much while pressed
_ANGLE_STEP = 2                     # degrees between cached frames


def _cov(d: float) -> float:
    """Pixel coverage for a signed distance in px (negative inside)."""
    a = 0.5 - d
    return 0.0 if a <= 0.0 else (1.0 if a >= 1.0 else a)


def _bracket_dist(x: float, y: float) -> float:
    """Distance from (x, y) to the corner stroke's centre line, in mark units."""
    t = min(max(y, _ARC_C), _LEG_END)
    d = math.hypot(x - 16.0, y - t)
    t = min(max(x, _ARC_C), _LEG_END)
    d = min(d, math.hypot(x - t, y - 16.0))
    if x <= _ARC_C and y <= _ARC_C:
        d = min(d, abs(math.hypot(x - _ARC_C, y - _ARC_C) - _ARC_R))
    return d


def render(heading: float, fill: float, scale: float, rgb: tuple):
    """One frame. Returns (n, x0, y0, w, h, bgra): an n x n canvas with the click
    point at pixel (n//2, n//2), and the drawn box (x0, y0, w, h) as premultiplied
    BGRA rows. `heading` is the direction the tip faces, in degrees (screen axes)."""
    k = _UNIT * scale
    gap, edge = _GAP * scale, _EDGE * scale
    hx = hy = _TIP - gap / k / math.sqrt(2)
    r_dot = _DOT_R * (1.0 + _SWELL * fill)
    pad = edge / k + 1.0 / k
    reach = max(math.hypot(_DOT_C - hx, _DOT_C - hy) + _DOT_R * (1.0 + _SWELL),
                math.hypot(_LEG_END + _HALF - hx, 16.0 - hy),
                math.hypot(16.0 - hx, _LEG_END + _HALF - hy)) + pad
    c = int(math.ceil(reach * k)) + 2
    n = 2 * c

    phi = math.radians(heading - _REST)
    cs, sn = math.cos(phi), math.sin(phi)
    # Box of the turned mark on the canvas.
    lo, hi = 16.0 - _HALF - pad, _DOT_C + r_dot + pad
    xs, ys = [], []
    for mx, my in ((lo, lo), (hi, lo), (lo, hi), (hi, hi)):
        dx, dy = (mx - hx) * k, (my - hy) * k
        xs.append(cs * dx - sn * dy)
        ys.append(sn * dx + cs * dy)
    x0, x1 = max(0, int(math.floor(min(xs))) + c), min(n, int(math.ceil(max(xs))) + c + 1)
    y0, y1 = max(0, int(math.floor(min(ys))) + c), min(n, int(math.ceil(max(ys))) + c + 1)
    w, h = x1 - x0, y1 - y0

    ar, ag, ab = rgb
    fill = min(1.0, max(0.0, fill))
    ring_mid = r_dot - _RING
    ring_px = 2 * _RING * k
    out = bytearray(w * h * 4)
    inv = 1.0 / k
    o = 0
    for py in range(y0, y1):
        dy = py + 0.5 - c
        for px in range(x0, x1):
            dx = px + 0.5 - c
            mx = (cs * dx + sn * dy) * inv + hx
            my = (-sn * dx + cs * dy) * inv + hy
            db = (_bracket_dist(mx, my) - _HALF) * k
            dd = math.hypot(mx - _DOT_C, my - _DOT_C)
            ddot = (dd - r_dot) * k
            # The ink edge rims the dot's outside only: an empty dot stays hollow.
            rim = max(ddot - edge, -(ddot + ring_px))
            a_edge = _INK_A * _cov(min(db - edge, rim))
            if a_edge <= 0.0 and not (fill > 0.0 and ddot < 0.5):
                o += 4
                continue
            # Ink edge, then the cream corner, then the accent filling the dot, then its ring.
            pa = a_edge
            pr = _INK[0] / 255.0 * a_edge
            pg = _INK[1] / 255.0 * a_edge
            pb = _INK[2] / 255.0 * a_edge
            a = _cov(db)
            if a > 0.0:
                pr = _CREAM[0] / 255.0 * a + pr * (1 - a)
                pg = _CREAM[1] / 255.0 * a + pg * (1 - a)
                pb = _CREAM[2] / 255.0 * a + pb * (1 - a)
                pa = a + pa * (1 - a)
            a = _cov(ddot)
            if a > 0.0:
                a *= fill
                pr = ar / 255.0 * a + pr * (1 - a)
                pg = ag / 255.0 * a + pg * (1 - a)
                pb = ab / 255.0 * a + pb * (1 - a)
                pa = a + pa * (1 - a)
                a = _cov((abs(dd - ring_mid) - _RING) * k)
                if a > 0.0:
                    pr = ar / 255.0 * a + pr * (1 - a)
                    pg = ag / 255.0 * a + pg * (1 - a)
                    pb = ab / 255.0 * a + pb * (1 - a)
                    pa = a + pa * (1 - a)
            out[o] = int(pb * 255 + 0.5)
            out[o + 1] = int(pg * 255 + 0.5)
            out[o + 2] = int(pr * 255 + 0.5)
            out[o + 3] = int(pa * 255 + 0.5)
            o += 4
    return n, x0, y0, w, h, bytes(out)


class _Spring:
    """Critically damped: arrives without overshoot."""

    def __init__(self, value: float, omega: float):
        self.x, self.v, self.target, self.w = value, 0.0, value, omega

    def step(self, dt: float) -> None:
        w = self.w
        while dt > 0:
            h = min(dt, 1 / 240)
            self.v += (w * w * (self.target - self.x) - 2 * w * self.v) * h
            self.x += self.v * h
            dt -= h

    def settled(self, eps: float) -> bool:
        return abs(self.target - self.x) < eps and abs(self.v) < eps * 10


# --- coordinates -----------------------------------------------------------------

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_api_ready = False
# Own handles, not ctypes.windll's shared ones: their argtypes can't be changed
# by other modules, nor lost when two threads load the same DLL at once.
_user32 = _gdi32 = _kernel32 = _shcore = None


def _api():
    if not _api_ready:
        with _api_lock:
            if not _api_ready:
                _load_api()


_api_lock = threading.Lock()


def _load_api():
    global _api_ready, _user32, _gdi32, _kernel32, _shcore
    _user32 = ctypes.WinDLL("user32")
    _gdi32 = ctypes.WinDLL("gdi32")
    _kernel32 = ctypes.WinDLL("kernel32")
    _shcore = ctypes.WinDLL("shcore")
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    u = _user32
    u.MonitorFromPoint.restype = wintypes.HANDLE
    u.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
    u.GetMonitorInfoW.restype = wintypes.BOOL
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                      ctypes.POINTER(wintypes.UINT),
                                                      ctypes.POINTER(wintypes.UINT)]
    _api_ready = True


def _to_physical(x: int, y: int):
    """(x, y) in the calling thread's coordinates -> physical pixels and the
    monitor's scale. Callers may be DPI-unaware, system-aware or per-monitor;
    mapping through the monitor's rectangle in both spaces is exact for all."""
    _api()
    u = _user32
    hmon = u.MonitorFromPoint(_POINT(int(x), int(y)), 2)          # NEAREST
    lo = _MONITORINFO(); lo.cbSize = ctypes.sizeof(_MONITORINFO)
    u.GetMonitorInfoW(hmon, ctypes.byref(lo))
    old = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))     # per-monitor v2
    try:
        ph = _MONITORINFO(); ph.cbSize = ctypes.sizeof(_MONITORINFO)
        u.GetMonitorInfoW(hmon, ctypes.byref(ph))
        dx, dy = wintypes.UINT(96), wintypes.UINT(96)
        _shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
    finally:
        if old:
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    lr, pr = lo.rcMonitor, ph.rcMonitor
    lw, lh = max(1, lr.right - lr.left), max(1, lr.bottom - lr.top)
    px = pr.left + (x - lr.left) * (pr.right - pr.left) / lw
    py = pr.top + (y - lr.top) * (pr.bottom - pr.top) / lh
    return int(round(px)), int(round(py)), max(1.0, dx.value / 96.0)


# --- the window and its thread ---------------------------------------------------

class _Pointer(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True, name="hearth-agent-cursor")
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.ok = False
        self.pos = None                 # physical click point
        self.scale = 1.0
        self.visible = False
        self.heading = _Spring(_REST, 28.0)
        self.fill = _Spring(0.0, 42.0)
        self.alpha = _Spring(0.0, 26.0)
        self.trail = None               # where the last heading was measured from
        self.moved_at = 0.0
        self.pulses = []                # [start, end or None while held]
        self.next_free = 0.0
        self.rgb = _accent()
        self.rgb_at = time.monotonic()
        self.cache = {}
        self.shown = False
        self.hwnd = None
        self.n = 0

    # called from any thread
    def place(self, x, y, scale):
        now = time.monotonic()
        with self.lock:
            if self.pos is None or not self.visible:
                self.trail = (x, y)
            else:
                tx, ty = self.trail
                if math.hypot(x - tx, y - ty) >= 3 * scale:
                    self.heading.target = self._toward(math.degrees(math.atan2(y - ty, x - tx)))
                    self.trail = (x, y)
                    self.moved_at = now
            self.pos, self.scale, self.visible = (x, y), scale, True
            self.alpha.target = 1.0
        self.wake.set()

    def press(self, down: bool):
        now = time.monotonic()
        with self.lock:
            if down:
                start = max(now, self.next_free)
                self.pulses.append([start, None])
            else:
                for p in self.pulses:
                    if p[1] is None:
                        p[1] = max(p[0] + 0.09, now)
                        self.next_free = p[1] + 0.07
                        break
        self.wake.set()

    def fade(self):
        with self.lock:
            self.visible = False
            self.alpha.target = 0.0
        self.wake.set()

    def _toward(self, deg):
        """The target angle nearest the current one, so it turns the short way."""
        cur = self.heading.x
        return cur + ((deg - cur + 180.0) % 360.0 - 180.0)

    # the thread
    def run(self):
        try:
            self._create()
            self.ok = True
        except Exception:
            if os.environ.get("HEARTH_AGENT_CURSOR_DEBUG"):
                import traceback; traceback.print_exc()
            self.ok = False
            self.ready.set()
            return
        self.ready.set()
        last = time.monotonic()
        while True:
            try:
                self._pump()
                now = time.monotonic()
                dt, last = min(0.05, now - last), now
                busy = self._step(now, dt)
                self._draw()
            except Exception:
                if os.environ.get("HEARTH_AGENT_CURSOR_DEBUG"):
                    import traceback; traceback.print_exc()
                busy = False
            self.wake.wait(1 / 120 if busy else 0.5)
            self.wake.clear()

    def _step(self, now, dt):
        with self.lock:
            if self.pos is not None and now - self.moved_at > 0.09:
                self.heading.target = self._toward(_REST)
            held = False
            for p in self.pulses:
                if p[0] <= now and (p[1] is None or now < p[1]):
                    held = True
            self.pulses = [p for p in self.pulses if p[1] is None or p[1] > now]
            self.fill.target = 1.0 if held else 0.0
            pending = bool(self.pulses)
        for s in (self.heading, self.fill, self.alpha):
            s.step(dt)
        if now - self.rgb_at > 1.0:
            self.rgb, self.rgb_at = _accent(), now
        return pending or not (self.heading.settled(0.2) and self.fill.settled(0.01)
                               and self.alpha.settled(0.01))

    def _create(self):
        _api()
        u, g = _user32, _gdi32
        try:
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        HWND, HDC, DWORD, INT, UINT = (wintypes.HWND, wintypes.HDC, wintypes.DWORD,
                                       ctypes.c_int, wintypes.UINT)
        u.CreateWindowExW.restype = HWND
        u.CreateWindowExW.argtypes = [DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD,
                                      INT, INT, INT, INT, HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, wintypes.LPVOID]
        u.GetDC.restype = HDC; u.GetDC.argtypes = [HWND]
        u.ReleaseDC.argtypes = [HWND, HDC]
        u.SetWindowPos.argtypes = [HWND, HWND, INT, INT, INT, INT, UINT]
        u.ShowWindow.argtypes = [HWND, INT]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.UpdateLayeredWindow.argtypes = [HWND, HDC, ctypes.c_void_p, ctypes.c_void_p,
                                          HDC, ctypes.c_void_p, wintypes.COLORREF,
                                          ctypes.c_void_p, DWORD]
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT, UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        g.CreateCompatibleDC.restype = HDC; g.CreateCompatibleDC.argtypes = [HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ; g.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]

        # LAYERED | TRANSPARENT (click-through) | NOACTIVATE | TOPMOST | TOOLWINDOW
        ws_ex = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000008 | 0x00000080
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        # DefWindowProcW itself is the class's proc: no Python callback to marshal.
        self._proc = ctypes.cast(u.DefWindowProcW, WNDPROC)
        hinst = _kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "HearthAgentCursor"
        u.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u.CreateWindowExW(ws_ex, "HearthAgentCursor", None, 0x80000000,
                                      0, 0, 1, 1, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed")
        self.msg = wintypes.MSG()
        self.dib = self.mem_dc = None

    def _canvas(self, n):
        """A top-down 32-bit DIB of n x n, made again when the size changes."""
        if n == self.n and self.mem_dc:
            return
        u, g = _user32, _gdi32

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = n, -n
        bmi.biPlanes, bmi.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        screen = u.GetDC(None)
        try:
            if not self.mem_dc:
                self.mem_dc = g.CreateCompatibleDC(screen)
            dib = g.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        finally:
            u.ReleaseDC(None, screen)
        if not dib:
            raise OSError("CreateDIBSection failed")
        g.SelectObject(self.mem_dc, dib)
        if self.dib:
            g.DeleteObject(self.dib)
        self.dib, self.bits, self.n = dib, bits.value, n

    def _frame(self, heading, fill, scale, rgb):
        key = (int(round(heading / _ANGLE_STEP)) * _ANGLE_STEP % 360,
               round(fill * 8) / 8, round(scale, 2), rgb)
        f = self.cache.get(key)
        if f is None:
            if len(self.cache) > 400:
                self.cache.clear()
            f = self.cache[key] = render(key[0], key[1], key[2], rgb)
        return f

    def _draw(self):
        with self.lock:
            pos, scale = self.pos, self.scale
        u = _user32
        if pos is None:
            return
        if self.alpha.x < 0.01 and self.alpha.target == 0.0:
            if self.shown:
                u.ShowWindow(self.hwnd, 0)                  # SW_HIDE
                self.shown = False
            return
        n, x0, y0, w, h, data = self._frame(self.heading.x, self.fill.x, scale, self.rgb)
        self._canvas(n)
        ctypes.memset(self.bits, 0, n * n * 4)
        row = w * 4
        for r in range(h):
            ctypes.memmove(self.bits + ((y0 + r) * n + x0) * 4, data[r * row:(r + 1) * row], row)

        class BLEND(ctypes.Structure):
            _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
                        ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]

        c = n // 2
        dst = _POINT(pos[0] - c, pos[1] - c)
        size = (ctypes.c_long * 2)(n, n)
        src = _POINT(0, 0)
        blend = BLEND(0, 0, int(max(0.0, min(1.0, self.alpha.x)) * 255), 1)   # AC_SRC_ALPHA
        screen = u.GetDC(None)
        try:
            u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(dst), ctypes.byref(size),
                                  self.mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), 2)
        finally:
            u.ReleaseDC(None, screen)
        if not self.shown:
            # HWND_TOPMOST; NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
            u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            self.shown = True

    def _pump(self):
        u = _user32
        while u.PeekMessageW(ctypes.byref(self.msg), None, 0, 0, 1):   # PM_REMOVE
            u.TranslateMessage(ctypes.byref(self.msg))
            u.DispatchMessageW(ctypes.byref(self.msg))


_pointer = None
_failed = False
_start_lock = threading.Lock()


def _get():
    """The running pointer, started on first use; None when unavailable."""
    global _pointer, _failed
    if _pointer is not None:
        return _pointer if _pointer.ok or not _pointer.ready.is_set() else None
    if _failed or not (_WIN and _ENABLED):
        return None
    with _start_lock:
        if _pointer is None:
            try:
                p = _Pointer()
                p.start()
                _pointer = p
            except Exception:
                _failed = True
                return None
    return _pointer


def show(x: int, y: int):
    """Put the pointer's tip at (x, y), in the caller's screen coordinates. Never
    raises and never blocks, so it can't disturb the click that follows."""
    try:
        p = _get()
        if p is None:
            return
        try:
            px, py, scale = _to_physical(x, y)
        except Exception:
            px, py, scale = int(x), int(y), 1.0
        p.place(px, py, scale)
    except Exception:
        pass


def press(down: bool):
    """The button went down or up at the pointer: fill the dot while it is held,
    with each press shown as its own pulse."""
    try:
        p = _get()
        if p is not None:
            p.press(down)
    except Exception:
        pass


def hide():
    try:
        if _pointer is not None:
            _pointer.fade()
    except Exception:
        pass
