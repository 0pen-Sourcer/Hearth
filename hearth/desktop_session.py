"""While JARVIS uses the desktop, the person can see it; when it's done, they get
the screen back.

The first desktop action of a turn starts a session: a thin glow in the accent
round the screen's edge and the activity pill saying what it's using, both kept
out of screenshots. When the turn ends the glow goes, the pill says Done, a short
chime plays, the window that was in front before is put back in front, and any
taskbar button left flashing by a window it raised is stopped.

Owned by Hearth's own process. With an agent program as the brain the tools run
in an MCP server that program starts and stops; that server reports desktop
actions here (/api/brain/desktop) rather than drawing its own.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Optional, Set

# Tools that act on the person's screen (looking at it alone doesn't count).
DESKTOP_TOOLS = {
    "open_app", "desktop_click", "desktop_type", "smart_click", "execute_actions",
    "computer_click", "computer_type", "computer_key", "computer_move", "computer_scroll",
    "computer_drag", "focus_window", "manage_window", "arrange_windows", "wait_for_element",
    "open_url", "open_in_browser",
}

_LOCK = threading.Lock()
_active = False
_return_to: Optional[int] = None
_raised: Set[int] = set()
_WIN = sys.platform == "win32"


def _enabled() -> bool:
    try:
        from .activity_hud import _enabled as hud_on
        return hud_on()
    except Exception:
        return _WIN


def begin(tool: str, args: Optional[Dict[str, Any]] = None) -> None:
    """A desktop action is starting. The first of a turn starts the session."""
    global _active, _return_to
    if tool not in DESKTOP_TOOLS or not _WIN:
        return
    with _LOCK:
        first = not _active
        _active = True
        if first:
            _return_to = _foreground()
    try:
        from . import activity_hud
        if first:
            from .approval import agent
            activity_hud.activity(f"{agent()} is using your computer")
        else:
            activity_hud.for_tool(tool, args or {})
    except Exception:
        pass
    if first and _enabled():
        _Glow.show()
        try:
            from . import cu_panel
            cu_panel.show()
        except Exception:
            pass


def raised(hwnd: int) -> None:
    """A window JARVIS brought forward, to stop its taskbar flashing at the end."""
    if hwnd:
        _raised.add(int(hwnd))


def end(said: str = "Done") -> bool:
    """The turn is over. Returns whether a session was running."""
    global _active, _return_to
    with _LOCK:
        if not _active:
            return False
        _active = False
        back, _return_to = _return_to, None
        flashing = list(_raised)
        _raised.clear()
    _Glow.hide()
    try:
        from . import activity_hud
        activity_hud.done(said)
    except Exception:
        pass
    try:
        from . import cu_panel
        cu_panel.finish(said)
    except Exception:
        pass
    _chime()
    for h in flashing:
        _stop_flash(h)
    if back:
        bring_to_front(back)
    return True


def is_active() -> bool:
    return _active


# ---------------------------------------------------------------- the turn, for the panel

_owner: Optional[object] = None


class _Emit:
    """The turn's emit, also feeding the panel its steps."""

    def __init__(self, emit, pending: Dict[str, Any]):
        self.emit, self.pending = emit, pending

    def __call__(self, kind: str, **f) -> Any:
        try:
            from . import cu_panel
            if kind == "tool_call" and f.get("name"):
                w = f.get("words")
                if not isinstance(w, dict):
                    from .step_words import words
                    w = words(f["name"], f.get("args"), str(f.get("says") or ""))
                cu_panel.step_start(f["name"], w.get("doing", ""), w.get("done", ""))
            elif kind == "tool_result" and f.get("name"):
                said = str(f.get("content") or "").lstrip().lower()
                cu_panel.step_end(f["name"], not said.startswith(("error", "not run")))
            elif kind == "permission_request" and f.get("id"):
                self.pending["id"] = f.get("id")
        except Exception:
            pass
        return self.emit(kind, **f) if self.emit else None

    def __getattr__(self, item):
        return getattr(self.emit, item)


def _deliver(req_id: str, decision: str) -> bool:
    """An answer given on the panel, handed to the window's own waiting question."""
    web = sys.modules.get("hearth.web")
    q = getattr(web, "_permission_queues", {}).get(req_id) if web else None
    if q is None:
        return False
    try:
        q.put(decision)
        return True
    except Exception:
        return False


def _asked(name: str, args: Dict[str, Any]) -> int:
    from . import cu_panel
    try:
        from .auto_review import why
        because = why(name, args)
    except Exception:
        because = ""
    return cu_panel.ask(name, args, because)


def _answered(name: str, args: Dict[str, Any], out: Any) -> Any:
    """A no leaves one quiet line among the panel's steps, however it was given."""
    d = out.get("decision") if isinstance(out, dict) else out
    if _active and d in ("deny", "never", "timeout", "aside"):
        try:
            from . import cu_panel
            from .approval import parts
            cu_panel.declined(parts(name, args or {})[0], aside=d == "aside")
        except Exception:
            pass
    return out


def _one_yes(name: str, args: Dict[str, Any]) -> bool:
    """Whether a step falls under the turn's one yes to using the computer. Opening
    a page has its own question, and anything that could send data out always asks."""
    if name not in DESKTOP_TOOLS or name in ("open_url", "open_in_browser"):
        return False
    try:
        from .tools import sends_out
        return not sends_out(name, args)
    except Exception:
        return False


def _said_yes(out: Any) -> bool:
    return (out.get("decision") if isinstance(out, dict) else out) in ("allow", "always")


def _check_wrap(check, emit, pending: Dict[str, Any]):
    """The turn's approval check, with the question also on the panel while it
    is up. Whichever answers first, the panel or the surface, wins. Desktop steps
    ask once a turn: the panel opens first, so the question is where the person
    is watching, and a yes covers the rest of them."""
    import asyncio
    import contextvars
    import inspect
    from . import cu_panel

    def settled(rid, ans):
        if rid and emit is not None:
            try:
                emit("permission_settled", id=rid, decision=ans)
            except Exception:
                pass

    if inspect.iscoroutinefunction(check):
        async def wrapped_async(name, args):
            if not _one_yes(name, args):
                return await ask_async(name, args)
            if pending.get("computer"):
                return "allow"
            begin(name, args)
            out = await ask_async(name, args)
            pending["computer"] = _said_yes(out)
            return out

        async def ask_async(name, args):
            if not _active:
                return await check(name, args)
            pending["id"] = None
            task = asyncio.ensure_future(check(name, args))
            await asyncio.wait({task}, timeout=0.25)
            if task.done():
                return _answered(name, args, task.result())
            tok = _asked(name, args)
            try:
                while not task.done():
                    ans = cu_panel.answer(tok)
                    if ans:
                        task.cancel()
                        settled(pending.get("id"), ans)
                        return _answered(name, args, ans)
                    await asyncio.wait({task}, timeout=0.05)
                return _answered(name, args, task.result())
            finally:
                cu_panel.clear_ask(tok)
        return wrapped_async

    def wrapped(name, args):
        if not _one_yes(name, args):
            return ask(name, args)
        if pending.get("computer"):
            return "allow"
        begin(name, args)
        out = ask(name, args)
        pending["computer"] = _said_yes(out)
        return out

    def ask(name, args):
        if not _active:
            return check(name, args)
        pending["id"] = None
        box: Dict[str, Any] = {}
        done = threading.Event()
        ctx = contextvars.copy_context()

        def run():
            try:
                box["r"] = ctx.run(check, name, args)
            except Exception as e:
                box["e"] = e
            finally:
                done.set()
        threading.Thread(target=run, daemon=True, name="hearth-approval").start()

        def result():
            if "e" in box:
                raise box["e"]
            return _answered(name, args, box.get("r"))
        if done.wait(0.25):
            return result()
        tok = _asked(name, args)
        try:
            while not done.wait(0.05):
                ans = cu_panel.answer(tok)
                if ans:
                    rid = pending.get("id")
                    if rid and _deliver(rid, ans) and done.wait(5.0):
                        out = result()               # the window keeps its own bookkeeping
                    else:
                        out = _answered(name, args, ans)
                    settled(rid, ans)
                    return out
            return result()
        finally:
            cu_panel.clear_ask(tok)
    return wrapped


def watch(prompt: str, kw: Dict[str, Any]):
    """Feed the panel from a turn: its steps, its questions, and Stop. Returns
    the turn's arguments to use and a token for unwatch(). One turn at a time:
    one that starts while another runs is left as it is."""
    global _owner
    tok = object()
    with _LOCK:
        if _owner is not None:
            return kw, None
        _owner = tok
    try:
        from . import cu_panel
        cu_panel.begin_turn(prompt)
        pending: Dict[str, Any] = {"id": None}
        out = dict(kw)
        emit = _Emit(kw.get("emit"), pending)
        out["emit"] = emit
        if kw.get("permission_check") is not None:
            out["permission_check"] = _check_wrap(kw["permission_check"], kw.get("emit"), pending)
        cancel = kw.get("should_cancel")
        out["should_cancel"] = lambda: bool(cancel and cancel()) or cu_panel.stop_requested()
        return out, tok
    except Exception:
        with _LOCK:
            _owner = None
        return kw, None


def turn_over(tok) -> None:
    """A turn ended. The session ends with the turn that watched it, or with any
    turn when none did; a turn that ran beside it leaves it running."""
    global _owner
    with _LOCK:
        if _owner is not None and tok is not _owner:
            return
        _owner = None
    end()


# ---------------------------------------------------------------- windows

def _foreground() -> Optional[int]:
    if not _WIN:
        return None
    try:
        import ctypes
        h = ctypes.windll.user32.GetForegroundWindow()
        return int(h) or None
    except Exception:
        return None


def bring_to_front(hwnd: int) -> bool:
    """Put a window in front in a way Windows accepts from a background process.
    A plain SetForegroundWindow is refused there and Windows flashes the
    taskbar button instead; a key press first lets the call through."""
    if not _WIN or not hwnd:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        if not u.IsWindow(hwnd):
            return False
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)                       # SW_RESTORE
        VK_MENU, KEYUP = 0x12, 0x0002
        u.keybd_event(VK_MENU, 0, 0, 0)                 # a press of Alt unlocks it
        u.keybd_event(VK_MENU, 0, KEYUP, 0)
        ok = bool(u.SetForegroundWindow(hwnd))
        u.BringWindowToTop(hwnd)
        _stop_flash(hwnd)
        return ok
    except Exception:
        return False


def _stop_flash(hwnd: int) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
        fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)    # FLASHW_STOP
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(fi))
    except Exception:
        pass


# ---------------------------------------------------------------- chime

_CHIME: Optional[str] = None


def _chime() -> None:
    """Two soft tones, the window's own 'done' sound, unless sounds are off."""
    global _CHIME
    if not _WIN:
        return
    try:
        from .web import _load_settings
        if _load_settings().get("sounds") is False:
            return
    except Exception:
        pass
    try:
        import math
        import winsound
        if not _CHIME or not os.path.exists(_CHIME):
            rate, frames = 22050, bytearray()
            total = int(rate * 0.36)
            samples = [0.0] * total
            for freq, start, dur in ((660.0, 0.0, 0.16), (880.0, 0.11, 0.22)):
                s0, n = int(start * rate), int(dur * rate)
                for i in range(n):
                    if s0 + i < total:
                        env = min(1.0, i / (0.01 * rate)) * math.exp(-5.0 * i / n)
                        samples[s0 + i] += 0.18 * env * math.sin(2 * math.pi * freq * i / rate)
            for v in samples:
                frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
            path = os.path.join(tempfile.gettempdir(), "hearth_done_chime.wav")
            with open(path, "wb") as f:
                f.write(b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
                        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
                        + b"data" + struct.pack("<I", len(frames)) + bytes(frames))
            _CHIME = path
        winsound.PlaySound(_CHIME, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


# ---------------------------------------------------------------- the glow

BREATH_S = 2.8          # one slow breath
FADE_S = 0.25           # in and out
LOW, HIGH = 0.72, 1.0   # how far the breath dims and brightens the edge


def breath(t: float) -> float:
    """The edge's strength t seconds into a session, LOW..HIGH, easing in and out."""
    import math
    return LOW + (HIGH - LOW) * (0.5 - 0.5 * math.cos(2 * math.pi * t / BREATH_S))


class _Glow:
    """A click-through, always-on-top window round the whole screen with a soft
    edge in the accent that breathes slowly, left out of screenshots and screen
    recordings. Everything that touches the window runs on its own thread."""
    _thread: Optional[threading.Thread] = None
    _hwnd: Optional[int] = None
    _want = False
    _ready = threading.Event()
    _painted: Optional[tuple] = None
    _accent: Optional[tuple] = None

    @classmethod
    def show(cls) -> None:
        try:
            from .voice_overlay import _saved_accent
            cls._accent = tuple(_saved_accent())       # read fresh: the theme's accent may have changed
        except Exception:
            cls._accent = (0xE0, 0x7B, 0x4C)
        cls._want = True
        if cls._thread is None or not cls._thread.is_alive():
            cls._ready.clear()
            cls._thread = threading.Thread(target=cls._run, daemon=True, name="hearth-edge-glow")
            cls._thread.start()
            cls._ready.wait(2.0)

    @classmethod
    def hide(cls) -> None:
        cls._want = False

    @classmethod
    def _run(cls) -> None:
        try:
            cls._hwnd = cls._build()
        except Exception:
            cls._hwnd = None
        cls._ready.set()
        if not cls._hwnd:
            return
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        msg = wintypes.MSG()
        level, shown, began = 0.0, False, 0.0
        while True:
            while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):      # PM_REMOVE
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            now = time.monotonic()
            if cls._want:
                if not shown:
                    if cls._accent != cls._painted:
                        cls._paint(cls._accent)
                    cls._set_alpha(0)
                    u.ShowWindow(cls._hwnd, 4)                          # SW_SHOWNOACTIVATE
                    shown, began = True, now
                level = min(1.0, level + 1.0 / (FADE_S * 30))
            else:
                level = max(0.0, level - 1.0 / (FADE_S * 30))
                if shown and level == 0.0:
                    u.ShowWindow(cls._hwnd, 0)                          # SW_HIDE
                    shown = False
            if shown:
                cls._set_alpha(int(255 * level * breath(now - began)))
                time.sleep(1 / 30)
            else:
                time.sleep(0.1)

    @classmethod
    def _set_alpha(cls, a: int) -> None:
        import ctypes
        ctypes.windll.user32.UpdateLayeredWindow(cls._hwnd, None, None, None, None, None, 0,
                                                 ctypes.byref(_BLEND(0, 0, max(0, min(255, a)), 1)), 2)

    @classmethod
    def _paint(cls, rgb: tuple) -> None:
        """Draw the edge in this colour: strongest at the border, fading ~30 px in,
        scaled with the display so it reads the same on a large screen."""
        import ctypes
        import numpy as np
        x0, y0, w, h = cls._geom
        r_, g_, b_ = rgb
        depth = 30 * max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
        ys = np.minimum(np.arange(h), np.arange(h)[::-1])[:, None]
        xs = np.minimum(np.arange(w), np.arange(w)[::-1])[None, :]
        d = np.minimum(ys, xs).astype(np.float32)
        a = (np.clip(1.0 - d / depth, 0.0, 1.0) ** 1.6 * 240.0).astype(np.uint16)
        img = np.zeros((h, w, 4), dtype=np.uint8)
        img[..., 0] = (b_ * a // 255).astype(np.uint8)          # premultiplied BGRA
        img[..., 1] = (g_ * a // 255).astype(np.uint8)
        img[..., 2] = (r_ * a // 255).astype(np.uint8)
        img[..., 3] = a.astype(np.uint8)
        ctypes.memmove(cls._bits, img.ctypes.data, w * h * 4)
        u = ctypes.windll.user32
        sdc = u.GetDC(None)
        u.UpdateLayeredWindow(cls._hwnd, sdc, ctypes.byref(_PT(x0, y0)), ctypes.byref(_SZ(w, h)), cls._mdc,
                              ctypes.byref(_PT(0, 0)), 0, ctypes.byref(_BLEND(0, 0, 0, 1)), 2)
        u.ReleaseDC(None, sdc)
        cls._painted = tuple(rgb)

    @classmethod
    def _build(cls) -> Optional[int]:
        import ctypes
        from ctypes import wintypes
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        x0, y0 = u.GetSystemMetrics(76), u.GetSystemMetrics(77)          # virtual screen
        w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        if w <= 0 or h <= 0:
            return None
        HWND, HDC, DWORD, INT, UINT = wintypes.HWND, wintypes.HDC, wintypes.DWORD, ctypes.c_int, wintypes.UINT
        u.CreateWindowExW.restype = HWND
        u.CreateWindowExW.argtypes = [DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD, INT, INT, INT, INT,
                                      HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u.GetDC.restype = HDC
        u.GetDC.argtypes = [HWND]
        u.ReleaseDC.argtypes = [HWND, HDC]
        u.UpdateLayeredWindow.argtypes = [HWND, HDC, ctypes.c_void_p, ctypes.c_void_p, HDC, ctypes.c_void_p,
                                          wintypes.COLORREF, ctypes.c_void_p, DWORD]
        u.ShowWindow.argtypes = [HWND, INT]
        g.CreateCompatibleDC.restype = HDC
        g.CreateCompatibleDC.argtypes = [HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", INT), ("cbWndExtra", INT),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]
        cls._proc = ctypes.cast(u.DefWindowProcW, WNDPROC)
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = cls._proc
        wc.hInstance = hinst
        wc.lpszClassName = "HearthEdgeGlow"
        u.RegisterClassW(ctypes.byref(wc))
        # LAYERED | TRANSPARENT (clicks pass through) | TOPMOST | NOACTIVATE | TOOLWINDOW
        ex = 0x00080000 | 0x00000020 | 0x00000008 | 0x08000000 | 0x00000080
        hwnd = u.CreateWindowExW(ex, "HearthEdgeGlow", None, 0x80000000, x0, y0, w, h, None, None, hinst, None)
        if not hwnd:
            return None
        try:
            from . import overlays
            overlays.register(int(hwnd))
        except Exception:
            pass

        class BMI(ctypes.Structure):
            _fields_ = [("biSize", DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", DWORD),
                        ("biSizeImage", DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", DWORD), ("biClrImportant", DWORD)]
        bmi = BMI()
        bmi.biSize, bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = ctypes.sizeof(BMI), w, -h, 1, 32
        bits = ctypes.c_void_p()
        sdc = u.GetDC(None)
        cls._mdc = g.CreateCompatibleDC(sdc)
        cls._dib = g.CreateDIBSection(sdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        g.SelectObject(cls._mdc, cls._dib)
        u.ReleaseDC(None, sdc)
        cls._bits, cls._geom, cls._hwnd = bits.value, (x0, y0, w, h), int(hwnd)
        return int(hwnd)


import ctypes as _ct  # noqa: E402


class _PT(_ct.Structure):
    _fields_ = [("x", _ct.c_long), ("y", _ct.c_long)]


class _SZ(_ct.Structure):
    _fields_ = [("cx", _ct.c_long), ("cy", _ct.c_long)]


class _BLEND(_ct.Structure):
    _fields_ = [("BlendOp", _ct.c_ubyte), ("BlendFlags", _ct.c_ubyte),
                ("SourceConstantAlpha", _ct.c_ubyte), ("AlphaFormat", _ct.c_ubyte)]
