"""Open a PyWebView window pointed at an existing Hearth server URL.

Used by `hearth.tray` to spawn the desktop window in its own process —
pywebview.start() must run on the main thread, which conflicts with
pystray's blocking event loop. Splitting into a subprocess is the
clean fix.

Standalone usage:
    python -m hearth.desktop_attach --url http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from typing import Optional


def _own_hwnd() -> int:
    """This process's main top-level window, or 0."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        cur_pid = ctypes.windll.kernel32.GetCurrentProcessId()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _l):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == cur_pid and not user32.GetWindow(hwnd, 4):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if area > 10000:
                    found.append((area, hwnd))
            return True

        user32.EnumWindows(_enum, 0)
        return max(found)[1] if found else 0
    except Exception:
        return 0


def _icon_file(look: str, accent: str = "") -> Optional[str]:
    """The window icon for a look. Windows wants an .ico for a window icon;
    elsewhere a .png works."""
    from . import look_icon
    return look_icon.file(look, accent or look_icon.saved()[1],
                          "ico" if sys.platform == "win32" else "png")


def _set_window_icon(look: str, accent: str = "") -> bool:
    """Swap the running window's title-bar and taskbar icon."""
    f = _icon_file(look, accent)
    hwnd = _own_hwnd()
    if not f or not hwnd or not f.lower().endswith(".ico"):
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        big = user32.LoadImageW(None, f, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, f, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, big)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, small)
        return bool(big or small)
    except Exception:
        return False


def _saved_look(url: str) -> str:
    """The saved look, read from the settings file: the backend can be too
    busy to answer while the window opens (a wake word starts both at once)."""
    try:
        import json as _json
        from .env import get as _get
        ws = _get("WORKSPACE") or __import__("hearth").default_workspace()
        from .look_icon import default_theme
        with open(os.path.join(ws, "settings.json"), encoding="utf-8") as f:
            s = _json.load(f) or {}
        return str(s.get("ui_theme") or default_theme(s))
    except Exception:
        pass
    try:
        import json as _json
        import urllib.request
        from .look_icon import default_theme
        with urllib.request.urlopen(url.rstrip("/") + "/api/settings", timeout=5) as r:
            s = _json.load(r) or {}
        return str(s.get("ui_theme") or default_theme(s))
    except Exception:
        return "dark"


def _force_foreground_win() -> bool:
    """Bring OUR OWN top-level window to the foreground on Windows, beating the
    foreground-lock that makes a bare SetForegroundWindow no-op (just flashes the
    taskbar). Find the window by THIS process id, not by title — WebView2 rewrites
    the window title from the page's <title>, so title matching is unreliable.
    Restore it, then AttachThreadInput to the current foreground thread so our
    SetForegroundWindow is actually honored. Returns False if this process owns no
    top-level window (e.g. it fell back to the browser), so the caller knows there
    was nothing native to surface."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cur_pid = kernel32.GetCurrentProcessId()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _l):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # Our process + a real top-level window (GW_OWNER=4 is 0 for top-level).
            # Do NOT require IsWindowVisible — a window hidden in the SYSTEM TRAY is
            # not "visible" yet is exactly the one to surface. Skip tooltips /
            # message-only helper windows by a real on-screen size instead.
            if pid.value == cur_pid and not user32.GetWindow(hwnd, 4):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if area > 10000:
                    found.append((area, hwnd))
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return False
        found.sort(reverse=True)          # largest area = the main window
        hwnd = found[0][1]
        user32.ShowWindow(hwnd, 5)        # SW_SHOW — un-hide a tray-hidden window
        user32.ShowWindow(hwnd, 9)        # SW_RESTORE — un-minimize without resizing
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_thread = kernel32.GetCurrentThreadId()
        attached = False
        try:
            if fg_thread and fg_thread != cur_thread:
                attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_thread, cur_thread, False)
        return True
    except Exception:
        return False


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hearth.desktop_attach")
    # Default to a running tray's backend — makes direct double-click of
    # Hearth-window.exe Just Work if the tray is already running on the
    # default port. Tray normally passes --url explicitly anyway.
    parser.add_argument("--url", default="http://127.0.0.1:8765/",
                        help="Backend URL to attach to. Default: localhost:8765.")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=840)
    args = parser.parse_args(argv)

    try:
        import webview  # type: ignore
    except ImportError:
        webbrowser.open(args.url)
        return 0

    # pywebview 6.x appends ALL three arch runtime dirs (win-arm64/x64/x86) to
    # PATH via interop_dll_path(), and raises FileNotFoundError('Cannot find
    # win-<arch>') if any of those folders is missing (a partial install / an
    # arch this box doesn't have). That exception crashes the whole native window
    # into the browser fallback — the "Cannot find win-arm64" in hearth_window.log
    # is exactly this, and why "surface the window" then does nothing (there IS no
    # native window). We only need the arch matching THIS machine, so tolerate a
    # missing per-arch FOLDER (win-*) while still raising for a missing real DLL.
    try:
        import webview.util as _wu
        _orig_interop = _wu.interop_dll_path
        def _safe_interop(dll_name, _o=_orig_interop):
            try:
                return _o(dll_name)
            except FileNotFoundError:
                if isinstance(dll_name, str) and dll_name.startswith("win-"):
                    return ""   # missing per-arch runtime dir — skip, not fatal
                raise
        _wu.interop_dll_path = _safe_interop
    except Exception:
        pass

    # PyWebView on Windows REQUIRES an .ico file for the window icon
    # (PNG raises System.ArgumentException). Prefer .ico, fall back to .png
    # (which works on Linux), else no icon at all.
    # Classic keeps the original H; the other looks use the hearth in their accent.
    icon_path = _icon_file(_saved_look(args.url))
    kwargs = dict(
        width=args.width, height=args.height, min_size=(900, 600),
        background_color="#0a0a0c",
        text_select=True, confirm_close=False,
    )
    def _browser_fallback(err: Exception) -> int:
        # The native window backend failed — most often the pythonnet/.NET
        # loader ("Failed to resolve Python.Runtime.Loader.Initialize") or a
        # missing WebView2 runtime on a fresh machine. The web UI is identical,
        # so open it in the default browser instead of hard-crashing. The tray
        # process keeps serving it at args.url.
        print(f"[desktop] native window unavailable ({type(err).__name__}: {err}); "
              f"opening the web UI in your browser instead: {args.url}")
        try:
            webbrowser.open(args.url)
        except Exception:
            pass
        return 0

    # Expose a tiny JS bridge so the page can raise its OWN native window. The
    # wake word flips an already-open (possibly buried) window into voice mode,
    # and only a foreground request from the window's own process reliably beats
    # Windows' foreground lock — a cross-process backend call cannot.
    _win_holder = {}

    class _WinApi:
        def set_icon(self, look, accent=""):
            return _set_window_icon(str(look or ""), str(accent or ""))

        def focus_window(self):
            win = _win_holder.get("win")
            # pywebview's own restore/show first (un-minimize, cross-platform)...
            if win is not None:
                try: win.restore()
                except Exception: pass
                try: win.show()
                except Exception: pass
            # ...then the robust Windows HWND path that actually beats the
            # foreground-lock (pywebview's on_top toggle just flashes the taskbar
            # from a buried/minimized state, which is why "surface" looked dead).
            if _force_foreground_win():
                return True
            # Fallback for non-Windows or if the HWND lookup missed.
            if win is not None:
                try:
                    win.on_top = True
                    import time as _t; _t.sleep(0.08)
                    win.on_top = False
                    return True
                except Exception:
                    pass
            return False

    grant_own_page(args.url)
    _win_holder["win"] = webview.create_window(
        "Inglen", url=args.url, js_api=_WinApi(), **kwargs)
    reload_if_stalled(_win_holder["win"], args.url)
    revive_backend(_win_holder["win"], args.url)
    # Keep the window's storage between launches; the default private window
    # forgets every remembered choice each time Hearth starts.
    keep = {}
    try:
        store = os.path.join(os.path.expanduser("~"), ".hearth", "webview")
        os.makedirs(store, exist_ok=True)
        keep = {"private_mode": False, "storage_path": store}
    except OSError:
        pass
    try:
        if icon_path:
            webview.start(gui=None, debug=False, icon=icon_path, **keep)
        else:
            webview.start(gui=None, debug=False, **keep)
    except TypeError:
        # Old pywebview without the icon kwarg — retry once without it.
        try:
            webview.start(gui=None, debug=False)
        except Exception as e:
            return _browser_fallback(e)
    except Exception as e:
        return _browser_fallback(e)
    return 0



def _answers(url: str, timeout: float = 2.0) -> bool:
    import socket
    import urllib.parse
    u = urllib.parse.urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _alive(pid: int) -> bool:
    try:
        from .bridge_status import _alive as alive
        return alive(pid)
    except Exception:
        return True


def revive_backend(win, url: str, every: float = 2.0, misses: int = 3, limit: int = 3,
                   answers=None, launch=None, sleep=None) -> threading.Thread:
    """Start Hearth's backend again when it stops. The window outlives the
    process that serves it; when native code (an audio driver, a model runtime)
    takes that process down, the window was left showing "Failed to fetch"
    until Hearth was restarted by hand. When the backend stops answering and
    its process is gone, start it again the way the tray started it, say so in
    the window, and reload the page once it answers. At most `limit` times."""
    import json as _json
    import subprocess
    import threading
    import time
    answers = answers or _answers
    sleep = sleep or time.sleep
    try:
        cmd = _json.loads(os.environ.get("HEARTH_BACKEND_CMD") or "null")
        pid = int(os.environ.get("HEARTH_BACKEND_PID") or 0)
    except (ValueError, TypeError):
        cmd, pid = None, 0

    def _say(text: str) -> None:
        try:
            win.evaluate_js("try{toast('Inglen', %s, 'warn', 9000)}catch(e){}" % _json.dumps(text))
        except Exception:
            pass

    def _start() -> bool:
        if launch:
            return bool(launch(cmd))
        try:
            subprocess.Popen(cmd, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=0x08000000 | 0x00000200 if sys.platform == "win32" else 0,
                             start_new_session=sys.platform != "win32")
            return True
        except OSError:
            return False

    def _watch() -> None:
        nonlocal pid
        down, revived = 0, 0
        while revived < limit:
            sleep(every)
            if answers(url):
                down = 0
                continue
            down += 1
            if down < misses or not cmd or (pid and _alive(pid)):
                continue
            _say("Inglen stopped unexpectedly. Starting it again...")
            if not _start():
                return
            revived += 1
            pid = 0
            for _ in range(60):
                sleep(1.0)
                if answers(url):
                    break
            try:
                win.load_url(url)
            except Exception:
                pass
            down = 0

    t = threading.Thread(target=_watch, daemon=True, name="hearth-revive")
    t.start()
    return t


def _origin(url: str) -> str:
    import urllib.parse
    u = urllib.parse.urlparse(str(url or ""))
    return f"{u.scheme}://{u.netloc}".lower()


# What the window's own page may use without the web view asking anyone: the
# mic for voice mode, the camera and reading the clipboard should it ask.
# Windows' own privacy switches stay the gate, as for any app on the PC.
GRANTED = ("microphone", "camera", "clipboardread")


def permission_for(kind: str, uri: str, own: str) -> bool:
    """Whether a permission the page asks for is given: only those above, only to Inglen's own address."""
    return _origin(uri) == _origin(own) and str(kind or "").strip().lower() in GRANTED


def grant_own_page(url: str) -> bool:
    """Answer the web view's permission requests in the host, so it never shows
    its own bubble (which names the local address). WebView2 (Windows) and
    WebKitGTK (Linux) are handled here; pywebview's Qt backend already allows
    capture to any page it shows; on macOS the page doesn't ask for the mic
    (voice mode listens through the PC's own mic there, see web.py's call.mic)."""
    if sys.platform == "win32":
        return _grant_webview2(url)
    if sys.platform.startswith("linux"):
        return _grant_gtk(url)
    return False


def _grant_gtk(url: str) -> bool:
    """WebKitGTK: media capture switched on; permission-request answered for every request."""
    try:
        from webview.platforms import gtk as _g
        webkit = _g.webkit
    except Exception:
        return False

    def _asked(view, request):
        try:
            kind = ""
            if isinstance(request, webkit.UserMediaPermissionRequest):
                kind = "camera" if request.props.is_for_video_device else "microphone"
            if permission_for(kind, view.get_uri(), url):
                request.allow()
            else:
                request.deny()
            return True
        except Exception:
            return False

    made = _g.BrowserView.__init__
    if getattr(made, "_own_page", False):
        return True

    def __init__(self, *a, **k):
        made(self, *a, **k)
        try:
            self.webview.get_settings().set_enable_media_stream(True)
            self.webview.connect("permission-request", _asked)
        except Exception:
            pass
    __init__._own_page = True
    _g.BrowserView.__init__ = __init__
    return True


def _grant_webview2(url: str) -> bool:
    """WebView2: PermissionRequested, which pywebview leaves unhandled."""
    try:
        from webview.platforms import winforms  # noqa: F401  (sets up .NET the way pywebview wants)
        from webview.platforms import edgechromium as _ec
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
    except Exception:
        return False

    def _asked(sender, args):
        try:
            ok = permission_for(str(args.PermissionKind), str(args.Uri), url)
            args.State = CoreWebView2PermissionState.Allow if ok else CoreWebView2PermissionState.Deny
        except Exception:
            try:
                args.State = CoreWebView2PermissionState.Deny
            except Exception:
                pass
        try:
            args.Handled = True                 # never the web view's own prompt
        except Exception:
            pass

    ready = _ec.EdgeChrome.on_webview_ready
    if getattr(ready, "_own_page", False):
        return True

    def on_webview_ready(self, sender, args):
        ready(self, sender, args)
        try:
            if args.IsSuccess:
                sender.CoreWebView2.PermissionRequested += _asked
        except Exception:
            pass
    on_webview_ready._own_page = True
    _ec.EdgeChrome.on_webview_ready = on_webview_ready
    return True


def reload_if_stalled(win, url: str, wait_s: float = 15.0, tries: int = 2) -> None:
    """Ask for the page again when it hasn't arrived. A request to Hearth's own
    server can stall or be reset on its way (some antivirus web shields sit on
    local traffic), which leaves the window blank for good otherwise."""
    import threading
    import time
    got = threading.Event()
    try:
        win.events.loaded += lambda *a: got.set()
    except Exception:
        return

    def _watch() -> None:
        for _ in range(tries):
            if got.wait(wait_s):
                return
            try:
                win.load_url(url)
            except Exception:
                return
    threading.Thread(target=_watch, daemon=True, name="hearth-page-watch").start()


if __name__ == "__main__":
    sys.exit(main())
