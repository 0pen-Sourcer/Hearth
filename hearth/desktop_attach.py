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
    here = os.path.dirname(os.path.abspath(__file__))
    asset_dir = os.path.join(os.path.dirname(here), "assets")
    # In PyInstaller bundles, _MEIPASS holds the unpacked tree
    bundle_assets = os.path.join(getattr(sys, "_MEIPASS", ""), "assets")
    icon_path = None
    for candidate in (
        os.path.join(asset_dir, "icon.ico"),
        os.path.join(bundle_assets, "icon.ico"),
        os.path.join(asset_dir, "icon.png"),
        os.path.join(bundle_assets, "icon.png"),
    ):
        if candidate and os.path.isfile(candidate):
            icon_path = candidate
            break
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

    _win_holder["win"] = webview.create_window(
        "Hearth", url=args.url, js_api=_WinApi(), **kwargs)
    try:
        if icon_path:
            webview.start(gui=None, debug=False, icon=icon_path)
        else:
            webview.start(gui=None, debug=False)
    except TypeError:
        # Old pywebview without the icon kwarg — retry once without it.
        try:
            webview.start(gui=None, debug=False)
        except Exception as e:
            return _browser_fallback(e)
    except Exception as e:
        return _browser_fallback(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
