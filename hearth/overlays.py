"""Inglen's own overlays: the steps panel, the status pill, the edge glow and
the pointer (and, for window reads, the voice dots and the capture cue).

The one rule: an overlay is never part of what the model sees. Each is left out
of every screen capture from the moment it is made (register), and skipped
wherever the accessibility tree or the window list is read (is_overlay).
Inglen's main window is not an overlay and stays visible to both. A look that is
about an overlay itself asks for them with `visible_to_model()`.

Also here: the plumbing they share (a per-pixel-alpha layered window, a canvas
drawn with numpy and GDI text) so each one looks and moves the same way.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import sys
import threading
from ctypes import wintypes
from typing import Callable, Dict, List, Optional, Set

_WIN = sys.platform == "win32"

CLASSES = frozenset({
    "HearthCUPanel", "HearthActivityHUD", "HearthEdgeGlow", "HearthAgentCursor",
    "HearthVoiceHUD", "HearthCaptureCue",
})

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

_lock = threading.Lock()
_mine: Set[int] = set()                      # overlay windows made by this process
_shown_depth = 0                             # visible_to_model() blocks open
_make_way: Dict[int, Callable[[int, int], None]] = {}


_u32 = None


def _user32():
    global _u32
    if _u32 is None:
        u = ctypes.WinDLL("user32")
        u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        _u32 = u
    return _u32


def affinity_of(hwnd: int) -> int:
    d = wintypes.DWORD(0)
    try:
        _user32().GetWindowDisplayAffinity(wintypes.HWND(int(hwnd)), ctypes.byref(d))
    except Exception:
        pass
    return d.value


def _affinity(hwnd: int, flag: int) -> bool:
    """Set a window's capture affinity. Windows 10 refuses it on a layered
    window (ERROR_NOT_ENOUGH_MEMORY), so the layered style comes off for the
    call and back on after; what the window shows is kept."""
    if not _WIN or not hwnd:
        return False
    try:
        u, h = _user32(), wintypes.HWND(int(hwnd))
        if u.SetWindowDisplayAffinity(h, flag):
            return True
        ex = u.GetWindowLongPtrW(h, -20)
        if not ex & 0x00080000:
            return False
        u.SetWindowLongPtrW(h, -20, ex & ~0x00080000)
        try:
            ok = bool(u.SetWindowDisplayAffinity(h, flag))
        finally:
            u.SetWindowLongPtrW(h, -20, ex)
        return ok
    except Exception:
        return False


def register(hwnd: int, make_way: Optional[Callable[[int, int], None]] = None) -> None:
    """An overlay window was made: out of captures from now on. `make_way` is
    told when an action is about to land at a physical screen point."""
    if not hwnd:
        return
    with _lock:
        _mine.add(int(hwnd))
        if make_way is not None:
            _make_way[int(hwnd)] = make_way
        shown = _shown_depth > 0
    _affinity(int(hwnd), WDA_NONE if shown else WDA_EXCLUDEFROMCAPTURE)


def forget(hwnd: int) -> None:
    with _lock:
        _mine.discard(int(hwnd or 0))
        _make_way.pop(int(hwnd or 0), None)


def class_of(hwnd: int) -> str:
    if not _WIN or not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(128)
        ctypes.windll.user32.GetClassNameW(wintypes.HWND(int(hwnd)), buf, 128)
        return buf.value
    except Exception:
        return ""


def is_overlay(hwnd) -> bool:
    """Whether a window is one of Inglen's overlays, in this process or another."""
    try:
        h = int(hwnd or 0)
    except (TypeError, ValueError):
        return False
    if not h:
        return False
    with _lock:
        if h in _mine:
            return True
    return class_of(h) in CLASSES


def set_visible(visible: bool) -> None:
    """Put this process's overlays into captures, or take them out again."""
    global _shown_depth
    with _lock:
        _shown_depth = max(0, _shown_depth + (1 if visible else -1))
        flag = WDA_NONE if _shown_depth else WDA_EXCLUDEFROMCAPTURE
        hwnds = list(_mine)
    for h in hwnds:
        _affinity(h, flag)


def _tell_hearth(tool: str, args: dict) -> None:
    """In an agent brain's tool server the overlays live in the Inglen that started it."""
    url, token = os.environ.get("HEARTH_BRAIN_URL", "").rstrip("/"), os.environ.get("HEARTH_BRAIN_TOKEN", "")
    if not url or not token:
        return
    try:
        import urllib.request
        body = json.dumps({"token": token, "tool": tool, "input": args}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(url + "/api/brain/desktop", data=body,
                               headers={"Content-Type": "application/json"}), timeout=1.5).read()
    except Exception:
        pass


@contextlib.contextmanager
def visible_to_model(wanted: bool = True):
    """A capture taken inside the block shows the overlays, for a look that is
    about one of them. Out again on the way out, however the block ends."""
    if not (wanted and _WIN):
        yield
        return
    remote = bool(os.environ.get("HEARTH_BRAIN_URL"))
    set_visible(True)
    if remote:
        _tell_hearth("_overlays", {"show": True})
    try:
        import time
        time.sleep(0.06)                    # DWM picks the change up on its next frame
        yield
    finally:
        set_visible(False)
        if remote:
            _tell_hearth("_overlays", {"show": False})


def make_way(x: int, y: int, physical: bool = False) -> None:
    """An action is about to land at (x, y): an overlay over that point lets it
    through. Never raises."""
    try:
        if not physical:
            try:
                from .agent_cursor import _to_physical
                x, y, _ = _to_physical(int(x), int(y))
            except Exception:
                pass
        with _lock:
            calls = list(_make_way.values())
        for fn in calls:
            try:
                fn(int(x), int(y))
            except Exception:
                pass
        if os.environ.get("HEARTH_BRAIN_URL"):
            _tell_hearth("_make_way", {"x": int(x), "y": int(y)})
    except Exception:
        pass


def filter_windows(hwnds: List[int], include: bool = False) -> List[int]:
    return list(hwnds) if include else [h for h in hwnds if not is_overlay(h)]


# ---------------------------------------------------------------- shared drawing

def mix(a: tuple, b: tuple, t: float) -> tuple:
    """a toward b by t."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def hexrgb(h: str) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


_accent_at = 0.0
_accent_rgb = (0xCF, 0x70, 0x89)


def accent() -> tuple:
    """The look's accent, read from settings at most once a second."""
    global _accent_at, _accent_rgb
    import time
    now = time.monotonic()
    if now - _accent_at > 1.0:
        _accent_at = now
        try:
            from .voice_overlay import _saved_accent
            _accent_rgb = tuple(_saved_accent())
        except Exception:
            pass
    return _accent_rgb


class Spring:
    """Critically damped: arrives without overshoot."""

    def __init__(self, value: float, omega: float):
        self.x, self.v, self.target, self.w = float(value), 0.0, float(value), omega

    def step(self, dt: float) -> None:
        w = self.w
        while dt > 0:
            h = min(dt, 1 / 240)
            self.v += (w * w * (self.target - self.x) - 2 * w * self.v) * h
            self.x += self.v * h
            dt -= h

    def snap(self, value: float) -> None:
        self.x, self.v, self.target = float(value), 0.0, float(value)

    def settled(self, eps: float) -> bool:
        return abs(self.target - self.x) < eps and abs(self.v) < eps * 10


def rrect_cov(np, h: int, w: int, x0: float, y0: float, x1: float, y1: float, r: float):
    """Antialiased coverage (h, w) of a rounded rectangle."""
    ys = np.arange(h, dtype=np.float32)[:, None] + 0.5
    xs = np.arange(w, dtype=np.float32)[None, :] + 0.5
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = max(0.0, (x1 - x0) / 2 - r), max(0.0, (y1 - y0) / 2 - r)
    qx = np.maximum(np.abs(xs - cx) - hx, 0.0)
    qy = np.maximum(np.abs(ys - cy) - hy, 0.0)
    inner = np.minimum(np.maximum(np.abs(xs - cx) - hx, np.abs(ys - cy) - hy), 0.0)
    d = np.sqrt(qx * qx + qy * qy) + inner - r
    return np.clip(0.5 - d, 0.0, 1.0)


def circle_cov(np, h: int, w: int, cx: float, cy: float, r: float):
    ys = np.arange(h, dtype=np.float32)[:, None] + 0.5
    xs = np.arange(w, dtype=np.float32)[None, :] + 0.5
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) - r
    return np.clip(0.5 - d, 0.0, 1.0)


def paint(np, img, cov, rgb: tuple, alpha: float = 1.0) -> None:
    """Lay a colour over an RGB float image where `cov` covers it."""
    a = (cov * alpha)[..., None]
    img[...] = img * (1 - a) + np.array(rgb, dtype=np.float32)[None, None, :] * a


def _box(img, x0, y0, x1, y1, clip=None):
    H, W = img.shape[:2]
    bx0, by0 = max(0, int(x0) - 1), max(0, int(y0) - 1)
    bx1, by1 = min(W, int(x1) + 2), min(H, int(y1) + 2)
    if clip:
        by0, by1 = max(by0, int(clip[0])), min(by1, int(clip[1]))
    return bx0, by0, bx1, by1


def fill_rrect(np, img, x0, y0, x1, y1, r, rgb: tuple, alpha: float = 1.0, clip=None) -> None:
    """A rounded rectangle, worked out only over its own box."""
    bx0, by0, bx1, by1 = _box(img, x0, y0, x1, y1, clip)
    if bx1 <= bx0 or by1 <= by0:
        return
    cov = rrect_cov(np, by1 - by0, bx1 - bx0, x0 - bx0, y0 - by0, x1 - bx0, y1 - by0, r)
    paint(np, img[by0:by1, bx0:bx1], cov, rgb, alpha)


def fill_circle(np, img, cx, cy, r, rgb: tuple, alpha: float = 1.0) -> None:
    bx0, by0, bx1, by1 = _box(img, cx - r, cy - r, cx + r, cy + r)
    if bx1 <= bx0 or by1 <= by0:
        return
    cov = circle_cov(np, by1 - by0, bx1 - bx0, cx - bx0, cy - by0, r)
    paint(np, img[by0:by1, bx0:bx1], cov, rgb, alpha)


class Surface:
    """A per-pixel-alpha layered window and the bitmap it shows. Lives on the
    thread that made it."""

    def __init__(self, cls_name: str, ex_style: int, wndproc=None, title: Optional[str] = None):
        self.cls_name = cls_name
        self.u = ctypes.WinDLL("user32")
        self.g = ctypes.WinDLL("gdi32")
        self._types()
        k = ctypes.WinDLL("kernel32")
        k.GetModuleHandleW.restype = wintypes.HMODULE
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        hinst = k.GetModuleHandleW(None)
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        self._proc = WNDPROC(wndproc) if wndproc else ctypes.cast(self.u.DefWindowProcW, WNDPROC)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        self.u.RegisterClassW(ctypes.byref(wc))
        self.hwnd = self.u.CreateWindowExW(ex_style, cls_name, title, 0x80000000,
                                           0, 0, 1, 1, None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW failed")
        self.mem_dc = None
        self.dib = None
        self.bits = None
        self.size = (0, 0)
        self.shown = False

    def _types(self):
        u, g = self.u, self.g
        HWND, HDC, DWORD, INT, UINT = wintypes.HWND, wintypes.HDC, wintypes.DWORD, ctypes.c_int, wintypes.UINT
        u.CreateWindowExW.restype = HWND
        u.CreateWindowExW.argtypes = [DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD, INT, INT, INT, INT,
                                      HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.GetDC.restype = HDC
        u.GetDC.argtypes = [HWND]
        u.ReleaseDC.argtypes = [HWND, HDC]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.UpdateLayeredWindow.argtypes = [HWND, HDC, ctypes.c_void_p, ctypes.c_void_p, HDC, ctypes.c_void_p,
                                          wintypes.COLORREF, ctypes.c_void_p, DWORD]
        u.SetWindowPos.argtypes = [HWND, HWND, INT, INT, INT, INT, UINT]
        u.ShowWindow.argtypes = [HWND, INT]
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT, UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [HWND, INT]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [HWND, INT, ctypes.c_ssize_t]
        g.CreateCompatibleDC.restype = HDC
        g.CreateCompatibleDC.argtypes = [HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.CreateFontW.restype = wintypes.HFONT
        g.CreateFontW.argtypes = [INT, INT, INT, INT, INT, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD,
                                  DWORD, DWORD, wintypes.LPCWSTR]
        g.SetTextColor.argtypes = [HDC, wintypes.COLORREF]
        g.SetBkMode.argtypes = [HDC, INT]
        u.DrawTextW.argtypes = [HDC, wintypes.LPCWSTR, INT, ctypes.POINTER(wintypes.RECT), UINT]
        u.DrawTextW.restype = INT
        g.GdiFlush.restype = wintypes.BOOL

    def pump(self) -> None:
        msg = wintypes.MSG()
        while self.u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):     # PM_REMOVE
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))

    def canvas(self, w: int, h: int) -> None:
        """A top-down 32-bit DIB of w x h, made again when the size changes."""
        if (w, h) == self.size and self.mem_dc:
            return

        class BMI(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        bmi = BMI()
        bmi.biSize, bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = ctypes.sizeof(BMI), w, -h, 1, 32
        bits = ctypes.c_void_p()
        sdc = self.u.GetDC(None)
        try:
            if not self.mem_dc:
                self.mem_dc = self.g.CreateCompatibleDC(sdc)
            dib = self.g.CreateDIBSection(sdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        finally:
            self.u.ReleaseDC(None, sdc)
        if not dib:
            raise OSError("CreateDIBSection failed")
        self.g.SelectObject(self.mem_dc, dib)
        if self.dib:
            self.g.DeleteObject(self.dib)
        self.dib, self.bits, self.size = dib, bits.value, (w, h)

    def array(self, np):
        """The bitmap as a writable (h, w, 4) BGRA array."""
        w, h = self.size
        buf = (ctypes.c_uint8 * (w * h * 4)).from_address(self.bits)
        return np.ctypeslib.as_array(buf).reshape(h, w, 4)

    def text(self, s: str, rect: tuple, font, rgb: tuple, flags: int) -> None:
        g = self.g
        old = g.SelectObject(self.mem_dc, font)
        g.SetBkMode(self.mem_dc, 1)                                  # TRANSPARENT
        g.SetTextColor(self.mem_dc, (rgb[2] << 16) | (rgb[1] << 8) | rgb[0])
        r = wintypes.RECT(*[int(v) for v in rect])
        self.u.DrawTextW(self.mem_dc, s, -1, ctypes.byref(r), flags)
        g.SelectObject(self.mem_dc, old)

    def measure(self, s: str, font, width: int, flags: int) -> tuple:
        """(w, h) DrawText would take for s inside `width`."""
        if not self.mem_dc:
            self.canvas(1, 1)
        g = self.g
        old = g.SelectObject(self.mem_dc, font)
        r = wintypes.RECT(0, 0, int(width), 0)
        self.u.DrawTextW(self.mem_dc, s or " ", -1, ctypes.byref(r), flags | 0x400)   # DT_CALCRECT
        g.SelectObject(self.mem_dc, old)
        return (r.right - r.left, r.bottom - r.top)

    def font(self, px: int, weight: int = 400, face: str = "Segoe UI"):
        # height, width, esc, orient, weight, italic, underline, strike, charset, out, clip, CLEARTYPE, pitch
        return self.g.CreateFontW(-int(px), 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, face)

    def present(self, x: int, y: int, alpha: float) -> None:
        """Show the bitmap at (x, y) with this overall opacity."""
        w, h = self.size

        class BLEND(ctypes.Structure):
            _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
                        ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]

        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        self.g.GdiFlush()
        sdc = self.u.GetDC(None)
        try:
            self.u.UpdateLayeredWindow(self.hwnd, sdc, ctypes.byref(PT(int(x), int(y))),
                                       ctypes.byref((ctypes.c_long * 2)(w, h)), self.mem_dc,
                                       ctypes.byref(PT(0, 0)), 0,
                                       ctypes.byref(BLEND(0, 0, int(max(0.0, min(1.0, alpha)) * 255), 1)), 2)
        finally:
            self.u.ReleaseDC(None, sdc)
        if not self.shown:
            # HWND_TOPMOST; NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
            self.u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            self.shown = True

    def hide(self) -> None:
        if self.shown:
            self.u.ShowWindow(self.hwnd, 0)
            self.shown = False

    def keep_on_top(self) -> None:
        if self.shown:
            self.u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def click_through(self, on: bool) -> None:
        ex = self.u.GetWindowLongPtrW(self.hwnd, -20)                # GWL_EXSTYLE
        new = (ex | 0x20) if on else (ex & ~0x20)                     # WS_EX_TRANSPARENT
        if new != ex:
            self.u.SetWindowLongPtrW(self.hwnd, -20, new)


def primary_work_area():
    """(left, top, right, bottom, scale) of the primary monitor's work area, in
    physical pixels. Call on a per-monitor-aware thread."""
    u = ctypes.WinDLL("user32")
    shcore = ctypes.WinDLL("shcore")

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    u.MonitorFromPoint.restype = wintypes.HANDLE
    u.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MI)]
    hmon = u.MonitorFromPoint(POINT(0, 0), 1)                         # PRIMARY
    mi = MI()
    mi.cbSize = ctypes.sizeof(MI)
    u.GetMonitorInfoW(hmon, ctypes.byref(mi))
    dx, dy = wintypes.UINT(96), wintypes.UINT(96)
    try:
        shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                            ctypes.POINTER(wintypes.UINT)]
        shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
    except Exception:
        pass
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom, max(1.0, dx.value / 96.0)


def per_monitor_thread() -> None:
    try:
        u = ctypes.WinDLL("user32")
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))           # per-monitor v2
    except Exception:
        pass
