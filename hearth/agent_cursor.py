"""A distinct on-screen marker that rides with Hearth's cursor while it controls
the desktop, so its actions always read apart from the user's own mouse.

Draws a violet ring + soft glow at a point via a pure-ctypes layered window (no
third-party deps, works in the packaged build). Best-effort and fully guarded: if
the overlay can't be created for any reason it silently no-ops, so it can NEVER
break an actual click. The marker is Windows-first for now (so is the desktop-
control layer); off Windows these calls are no-ops and control still works, just
without the marker yet. Toggle off with HEARTH_AGENT_CURSOR=0.
"""
from __future__ import annotations

import os
import sys
import math
import ctypes
from ctypes import wintypes

_WIN = sys.platform == "win32"
_ENABLED = os.environ.get("HEARTH_AGENT_CURSOR", "1") not in ("0", "false", "no")

# Brand violet (matches the app): #8b5cf6.
_R, _G, _B = 139, 92, 246
_SIZE = 46                      # overlay is _SIZE x _SIZE px, centered on the cursor
_OUT = _SIZE / 2 - 2            # ring outer radius
_IN = _OUT - 5                  # ring inner radius

_hwnd = None
_inited = False
_ok = False


def _build():
    """Register the class, create the layered window, blit the ring bitmap once.
    Returns True on success. Any failure leaves _ok False and callers no-op."""
    global _hwnd, _inited, _ok
    if _inited:
        return _ok
    _inited = True
    if not (_WIN and _ENABLED):
        return False
    try:
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32

        # Declare signatures so 64-bit handle returns aren't truncated to 32 bits
        # and DWORD/handle args (0x80000000, HWND_TOPMOST = -1) don't overflow the
        # default c_int — the source of the ArgumentError otherwise.
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
        g.CreateCompatibleDC.restype = HDC; g.CreateCompatibleDC.argtypes = [HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ; g.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]

        WS_EX = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000008 | 0x00000080
        # LAYERED | TRANSPARENT(click-through) | TOPMOST | NOACTIVATE | TOOLWINDOW
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        # Point the class straight at DefWindowProcW (cast to the proc type) —
        # no hand-written Python callback to marshal, so a 64-bit LPARAM/LRESULT
        # can't overflow. Keep a ref so it isn't GC'd.
        _build._proc = ctypes.cast(u.DefWindowProcW, WNDPROC)
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = _build._proc
        wc.hInstance = hinst
        wc.lpszClassName = "HearthAgentCursor"
        u.RegisterClassW(ctypes.byref(wc))
        _hwnd = u.CreateWindowExW(WS_EX, "HearthAgentCursor", None, 0x80000000,
                                  0, 0, _SIZE, _SIZE, None, None, hinst, None)
        if not _hwnd:
            return False

        # 32-bit premultiplied-ARGB DIB, drawn by hand (a ring + soft glow).
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = _SIZE
        bmi.biHeight = -_SIZE            # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        bits = ctypes.c_void_p()
        screen_dc = u.GetDC(None)
        mem_dc = g.CreateCompatibleDC(screen_dc)
        dib = g.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0,
                                 ctypes.byref(bits), None, 0)
        old = g.SelectObject(mem_dc, dib)

        buf = (ctypes.c_ubyte * (_SIZE * _SIZE * 4)).from_address(bits.value)
        cx = cy = _SIZE / 2.0
        for y in range(_SIZE):
            for x in range(_SIZE):
                d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                if d <= _IN:                       # soft-filled center
                    a = 0.30
                elif d <= _OUT:                    # bright ring
                    a = 1.0 - abs((d - (_IN + _OUT) / 2) / ((_OUT - _IN) / 2)) * 0.15
                elif d <= _OUT + 3:                # outer glow falloff
                    a = max(0.0, 1.0 - (d - _OUT) / 3) * 0.4
                else:
                    a = 0.0
                al = int(max(0.0, min(1.0, a)) * 235)
                o = (y * _SIZE + x) * 4            # BGRA, premultiplied
                buf[o + 0] = _B * al // 255
                buf[o + 1] = _G * al // 255
                buf[o + 2] = _R * al // 255
                buf[o + 3] = al

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        blend = BLENDFUNCTION(0, 0, 255, 1)        # AC_SRC_ALPHA
        src = POINT(0, 0)
        size = SIZE(_SIZE, _SIZE)
        dst = POINT(0, 0)
        u.UpdateLayeredWindow(_hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
                              mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        _build._keep = (dib, mem_dc, old)          # keep GDI objects alive
        u.ReleaseDC(None, screen_dc)
        _ok = True
        return True
    except Exception:
        if os.environ.get("HEARTH_AGENT_CURSOR_DEBUG"):
            import traceback; traceback.print_exc()
        _ok = False
        return False


def show(x: int, y: int):
    """Position the marker centered on (x, y) and make it visible. No-op on any
    failure — never raises, so it can't disturb the click that follows."""
    try:
        if not _build():
            return
        u = ctypes.windll.user32
        # SWP_NOSIZE|NOACTIVATE|SHOWWINDOW, HWND_TOPMOST(-1)
        u.SetWindowPos(_hwnd, -1, int(x) - _SIZE // 2, int(y) - _SIZE // 2,
                       0, 0, 0x0001 | 0x0010 | 0x0040)
    except Exception:
        pass


def hide():
    try:
        if _ok and _hwnd:
            ctypes.windll.user32.ShowWindow(_hwnd, 0)   # SW_HIDE
    except Exception:
        pass
