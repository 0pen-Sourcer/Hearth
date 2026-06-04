"""Hearth system tray app — lives in the notification area.

Runs the HTTP backend in the background, shows a tray icon with a menu,
and lets the user open the desktop window on demand. Designed to be the
"always-on" entry point — autostart this on boot via the Startup folder
and Hearth is one click away whenever you need it.

Usage:
    python -m hearth.tray                    # tray-only mode
    python -m hearth.tray --open             # open desktop window immediately too
    python -m hearth.tray --port 8765        # explicit port

Setup (one-shot from PowerShell, do these once):
    .\\.venv\\Scripts\\python.exe -m hearth.install_shortcuts
       # ↑ creates Desktop shortcut + Startup-folder shortcut

Dependencies:
    pip install pystray pillow
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Optional

from . import web as web_backend
from . import singleton
from .tools import WORKSPACE


def _icon_image():
    """Load assets/icon.png if present; fall back to a simple violet H tile."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(os.path.dirname(here), "assets", "icon.png")
    if os.path.isfile(icon_path):
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception:
            pass
    # Fallback — generate a 64x64 violet "H" on dark bg
    img = Image.new("RGBA", (64, 64), (10, 10, 12, 255))
    draw = ImageDraw.Draw(img)
    # Simple H letterform — three rectangles
    accent = (139, 92, 246, 255)
    # Left vertical
    draw.rectangle([(14, 12), (24, 52)], fill=accent)
    # Right vertical
    draw.rectangle([(40, 12), (50, 52)], fill=accent)
    # Crossbar (slightly higher than center — gives it character)
    draw.rectangle([(14, 28), (50, 36)], fill=accent)
    return img


_server_url: str = ""
_desktop_proc: Optional[subprocess.Popen] = None


def _open_desktop_window():
    """Open the PyWebView native window as a SUBPROCESS.

    PyWebView's `webview.start()` must run on the main thread, which doesn't
    play with pystray's blocking event loop. Sidestepping by spawning the
    desktop module in its own python process — it talks to the same backend
    over HTTP at `_server_url` so no state is duplicated.

    In FROZEN (PyInstaller) mode, there's no venv python to spawn, so we
    just open the default browser instead — same UI, just less native chrome.
    """
    global _desktop_proc
    if _desktop_proc and _desktop_proc.poll() is None:
        return

    # Combined flags: CREATE_NO_WINDOW suppresses the brief cmd console
    # flash users see when subprocess.Popen launches a child on Windows.
    # Without this, double-clicking Hearth.exe makes a quick black box pop
    # up "looks like a virus" before the GUI shows.
    _CREATE_NO_WINDOW = 0x08000000
    _CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    _spawn_flags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP

    # Frozen exe — spawn the sibling _HearthWindow.exe that lives next to
    # us in dist/Hearth/. Falls back to browser if it's missing.
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        window_exe = os.path.join(exe_dir, "_HearthWindow.exe")
        if not os.path.isfile(window_exe):
            window_exe = os.path.join(exe_dir, "Hearth-window.exe")
        if os.path.isfile(window_exe):
            try:
                _desktop_proc = subprocess.Popen(
                    [window_exe, "--url", _server_url],
                    creationflags=_spawn_flags,
                )
                return
            except Exception as e:
                print(f"[hearth.tray] could not spawn _HearthWindow.exe: {e}", file=sys.stderr)
        webbrowser.open(_server_url)
        return

    # Dev mode — locate venv python (prefer pythonw to suppress console)
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    venv_pythonw = os.path.join(repo_root, ".venv", "Scripts", "pythonw.exe")
    venv_python = os.path.join(repo_root, ".venv", "Scripts", "python.exe")
    py = venv_pythonw if os.path.isfile(venv_pythonw) else (
        venv_python if os.path.isfile(venv_python) else sys.executable)
    try:
        _desktop_proc = subprocess.Popen(
            [py, "-m", "hearth.desktop_attach", "--url", _server_url],
            creationflags=_spawn_flags,
        )
    except Exception as e:
        print(f"[hearth.tray] could not spawn desktop window: {e}", file=sys.stderr)
        webbrowser.open(_server_url)


def _open_browser():
    webbrowser.open(_server_url)


def _open_workspace():
    """Open the ~/Jarvis folder in Explorer / Finder."""
    if sys.platform == "win32":
        os.startfile(WORKSPACE)  # type: ignore
    elif sys.platform == "darwin":
        os.system(f"open '{WORKSPACE}'")
    else:
        os.system(f"xdg-open '{WORKSPACE}'")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.tray",
        description="Hearth system tray app.",
    )
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP backend port. 0 = auto-pick (default).")
    parser.add_argument("--open", action="store_true",
                        help="Open the desktop window immediately on launch.")
    parser.add_argument("--wake", action="store_true",
                        help="Start with wake-word listener enabled (say 'Jarvis' to open).")
    args = parser.parse_args(argv)

    # Single-instance check FIRST — otherwise clicking Hearth.exe N times
    # creates N tray icons. If another Hearth is running, ask it to surface
    # its window and exit.
    if not args.port:
        primary, _existing_port = singleton.acquire_or_defer(singleton.DEFAULT_PORT)
        if not primary:
            singleton.announce_secondary_and_exit()

    # Start backend
    global _server_url
    port = args.port or singleton.DEFAULT_PORT
    # If user passed --port OR our singleton-preferred port is unexpectedly
    # taken (e.g. it WAS bound but the Hearth instance died between checks),
    # walk forward to find a free one.
    import socket as _sk
    for off in range(0, 12):
        p = port + off
        try:
            with _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
                port = p
                break
        except OSError:
            continue
    server = web_backend.serve(host="127.0.0.1", port=port)
    _server_url = f"http://127.0.0.1:{port}/"
    time.sleep(0.3)

    print(f"[hearth.tray] backend on {_server_url}")

    # pystray
    try:
        import pystray  # type: ignore
    except ImportError:
        print("[hearth.tray] FATAL: pystray not installed.", file=sys.stderr)
        print("[hearth.tray] Run: pip install pystray pillow", file=sys.stderr)
        return 1

    img = _icon_image()

    # Wake-word listener — optional, off by default. Fires _open_desktop_window
    # when the user says "Jarvis" / "hey jarvis" etc.
    wake_listener = None
    wake_state = {"on": False}

    def _start_wake():
        nonlocal wake_listener
        try:
            from .wake import WakeListener
        except ImportError:
            return False
        if wake_listener and wake_listener.is_active():
            return True

        def on_wake(phrase: str):
            print(f"[hearth.tray] wake: '{phrase}' — opening window", flush=True)
            _open_desktop_window()

        wake_listener = WakeListener(on_wake=on_wake)
        ok = wake_listener.start()
        if ok:
            wake_state["on"] = True
        return ok

    def _stop_wake():
        nonlocal wake_listener
        if wake_listener:
            wake_listener.stop()
            wake_listener = None
        wake_state["on"] = False

    def on_open(icon, item):
        _open_desktop_window()

    def on_browser(icon, item):
        _open_browser()

    def on_workspace(icon, item):
        _open_workspace()

    def on_toggle_wake(icon, item):
        if wake_state["on"]:
            _stop_wake()
        else:
            if not _start_wake():
                # Surface failure via a balloon
                try:
                    icon.notify("Wake word needs faster-whisper + a working mic", "Wake unavailable")
                except Exception:
                    pass
        icon.update_menu()

    def on_quit(icon, item):
        _stop_wake()
        icon.stop()
        server.shutdown()
        # Make ABSOLUTELY sure the llama_cpp.server child dies. The Windows
        # Job Object will catch this on hard exits, but a clean Quit path
        # should also call stop_builtin so port 1234 is freed before the
        # next launch (so LM Studio "address already in use" stops happening).
        try:
            from . import llmserver
            llmserver.stop_builtin()
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem("Open Hearth", on_open, default=True),
        pystray.MenuItem("Open in browser", on_browser),
        pystray.MenuItem("Open workspace folder", on_workspace),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: f"Wake word: {'on' if wake_state['on'] else 'off'}",
            on_toggle_wake,
            checked=lambda item: wake_state["on"],
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("hearth", img, "Hearth — local AI", menu)
    # Auto-open the window if --open OR running as a bundled exe (so users
    # who double-click Hearth.exe get a window, not just a tray icon).
    if args.open or getattr(sys, "frozen", False):
        threading.Timer(0.5, _open_desktop_window).start()
    if args.wake:
        threading.Timer(1.0, _start_wake).start()

    # Signal handlers — Ctrl-C from the terminal MUST also free port 1234.
    # Without this, the user kills Hearth from the terminal and the orphan
    # llama_cpp.server keeps the port until reboot. Belt-and-suspenders with
    # the Job Object in llmserver.py.
    #
    # NOTE: don't call sys.exit() from this handler. On Windows pystray
    # runs its message loop via a ctypes WNDPROC callback; if a signal
    # fires while that callback is on the stack, raising SystemExit
    # propagates back into ctypes and Python prints "Exception ignored on
    # calling ctypes callback function". icon.stop() is enough — it
    # unblocks icon.run() in the main thread, which then returns from
    # main() naturally.
    import signal as _signal
    def _bye(*_a):
        try:
            from . import llmserver
            llmserver.stop_builtin()
        except Exception:
            pass
        try: _stop_wake()
        except Exception: pass
        try: server.shutdown()
        except Exception: pass
        try: icon.stop()
        except Exception: pass
    _signal.signal(_signal.SIGINT, _bye)
    _signal.signal(_signal.SIGTERM, _bye)

    icon.run()
    _stop_wake()
    server.shutdown()
    # Final safety net — atexit also fires, but call explicitly so port 1234
    # is reliably free before this main() returns.
    try:
        from . import llmserver
        llmserver.stop_builtin()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
