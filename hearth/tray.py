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


def _look() -> tuple:
    """The window's theme and accent, as it saved them in settings."""
    try:
        s = web_backend._load_settings() or {}
        from .look_icon import default_theme
        return (str(s.get("ui_theme") or default_theme(s)), str(s.get("ui_accent") or "rose"))
    except Exception:
        return ("dark", "rose")


def _icon_for_look(theme: str, accent: str):
    from . import look_icon
    # Drawn at 64 for sharpness but shown at 16 to 24 in the tray.
    return look_icon.image(theme, accent, 64, shown=20) or _icon_image()


def _icon_image():
    """The Nook in Rose, drawn here; None without Pillow."""
    try:
        from . import look_icon
        return look_icon.draw("rose", 64, shown=20)
    except Exception:
        return None


_server_url: str = ""
_desktop_proc: Optional[subprocess.Popen] = None


def _signal_voice_open() -> None:
    """Wake fired but a window is already open — ask the running GUI to enter
    voice mode in place (it can't be done by spawning a second ?voice=1 window).
    Sets a one-shot flag the GUI's /api/state poll picks up."""
    if not _server_url:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            _server_url.rstrip("/") + "/api/voice/open",
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception as e:
        print(f"[hearth.tray] voice-open signal failed: {e}", file=sys.stderr)


def _signal_open_chat(cid: str) -> None:
    """Ask the open window to show a chat (or start one: "new")."""
    if not _server_url:
        return
    try:
        import json as _json
        import urllib.request
        req = urllib.request.Request(_server_url.rstrip("/") + "/api/window/chat",
                                     data=_json.dumps({"id": cid}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def open_chat(cid: str) -> None:
    """Open the window on a chat, or on a new one ("new")."""
    if _desktop_proc and _desktop_proc.poll() is None:
        _signal_open_chat(cid)
    else:
        _open_desktop_window(chat=cid)


def _dpi_aware() -> None:
    """Draw the tray's menu at the screen's real resolution. Without this Windows
    draws it at 100% and stretches it on a scaled display, so the text blurs."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per monitor v2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _menus_dark(theme: str) -> bool:
    """Whether the menu should be dark: as Inglen's own look is, and for System
    as Windows' app mode is."""
    if theme == "light":
        return False
    if theme == "system" and sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return not winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
        except Exception:
            return True
    return True


def _dark_menus(theme: str = "") -> None:
    """Native menus in the look's mode, as other apps' tray menus are
    (uxtheme's SetPreferredAppMode/FlushMenuThemes, Windows 10 1903 and later)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ux = ctypes.WinDLL("uxtheme")
        ux[135](2 if _menus_dark(theme or _look()[0]) else 3)   # ForceDark / ForceLight
        ux[136]()                                                # FlushMenuThemes
    except Exception:
        pass


def _wake_name() -> str:
    try:
        from .persona import NAME
        return str(NAME).title()
    except Exception:
        return "Jarvis"


def _menu_chats(limit: int = 12):
    """Pinned chats and the newest others, for the tray menu."""
    try:
        from . import chat_sync
        rows = [c for c in chat_sync.listing(60) if c.get("count")]
    except Exception:
        return [], []
    pinned = [c for c in rows if c.get("pinned")][:5]
    rest = [c for c in rows if not c.get("pinned")][:limit]
    return pinned, rest


def _short(title: str, n: int = 40) -> str:
    t = " ".join(str(title or "Untitled").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "\u2026"


def chat_menu_item(c: dict):
    """A tray menu row that opens one chat. pystray accepts an action of at most
    two parameters, so the chat id is closed over, not a default argument."""
    import pystray
    cid = c["id"]

    def _open(icon, item):
        open_chat(cid)
    return pystray.MenuItem(_short(c.get("title")), _open)


def _open_desktop_window(voice: bool = False, chat: str = ""):
    """Open the PyWebView native window as a SUBPROCESS.

    voice=True appends ?voice=1 so the GUI auto-starts voice mode on load (the
    wake word + tray 'Voice mode' use this — say the word, the window opens
    already listening with the HUD up).

    PyWebView's `webview.start()` must run on the main thread, which doesn't
    play with pystray's blocking event loop. Sidestepping by spawning the
    desktop module in its own python process — it talks to the same backend
    over HTTP at `_server_url` so no state is duplicated.

    In FROZEN (PyInstaller) mode, there's no venv python to spawn, so we
    just open the default browser instead — same UI, just less native chrome.
    """
    global _desktop_proc
    if _desktop_proc and _desktop_proc.poll() is None:
        # Window already open — we can't spawn a second ?voice=1 window. For a
        # voice request (wake word / tray Voice mode) signal the running GUI to
        # enter voice mode IN PLACE — it polls /api/state for the flag and flips
        # itself into the HUD. Plain "Open" just no-ops; the window's already up.
        if voice:
            _signal_voice_open()
        return

    url = _server_url + ("?voice=1" if voice else (f"?chat={chat}" if chat else ""))

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
                    [window_exe, "--url", url],
                    creationflags=_spawn_flags, env=_revive_env([sys.executable]),
                )
                return
            except Exception as e:
                print(f"[hearth.tray] could not spawn _HearthWindow.exe: {e}", file=sys.stderr)
        webbrowser.open(url)
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
            [py, "-m", "hearth.desktop_attach", "--url", url],
            creationflags=_spawn_flags, env=_revive_env([py, "-m", "hearth.tray"]),
        )
    except Exception as e:
        print(f"[hearth.tray] could not spawn desktop window: {e}", file=sys.stderr)
        webbrowser.open(url)


def _revive_env(cmd: list) -> dict:
    """What the window needs to start this backend again if it stops: how it
    was started (no window, the same default port) and its process id."""
    import json as _json
    env = dict(os.environ)
    env["HEARTH_BACKEND_CMD"] = _json.dumps(cmd)
    env["HEARTH_BACKEND_PID"] = str(os.getpid())
    return env


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
    from .crashlog import enable as _crashlog
    _crashlog("tray")                 # the window's server runs in this process
    parser = argparse.ArgumentParser(
        prog="python -m hearth.tray",
        description="Inglen system tray app.",
    )
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP backend port. 0 = auto-pick (default).")
    parser.add_argument("--open", action="store_true",
                        help="Open the desktop window immediately on launch.")
    parser.add_argument("--wake", action="store_true",
                        help="Start with wake-word listener enabled (say the wake word to open).")
    args = parser.parse_args(argv)

    # Single-instance check FIRST — otherwise clicking Hearth.exe N times
    # creates N tray icons. If another Hearth is running, ask it to surface
    # its window and exit.
    if not args.port:
        primary, _existing_port = singleton.acquire_or_defer(singleton.DEFAULT_PORT)
        if not primary:
            singleton.announce_secondary_and_exit()

    # Shortcuts made before the rename say Hearth with the old icon; an update
    # can't change them, so the installed app renames its own once.
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        def _rebrand():
            try:
                from .install_shortcuts import rebrand
                rebrand()
            except Exception:
                pass
        threading.Thread(target=_rebrand, daemon=True).start()

    # Start backend
    global _server_url
    port = args.port or singleton.DEFAULT_PORT
    import socket as _sk

    # Try to bind the canonical port. If it's taken and we DIDN'T pass --port
    # explicitly, that means a race-condition let another Hearth slip past
    # singleton.acquire_or_defer between probe + bind — bail rather than
    # quietly starting on 8766 as a phantom parallel instance (this was the
    # "two Hearth trays running, one making ghost tool calls" bug).
    if not args.port:
        try:
            with _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            # Free — release immediately, real server binds next
        except OSError:
            # Race-lost. The other instance won; ask it to surface, exit clean.
            print(
                f"Inglen is already on port {port} (race-condition during boot — "
                f"another instance won). Surfacing the existing window.",
                flush=True,
            )
            try:
                singleton._focus_existing(port)
            except Exception:
                pass
            sys.exit(0)
    else:
        # User explicitly asked for a port — walk forward if it's taken.
        # This is the supported "I want a second instance for testing" path.
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

    # Let /api/focus open the window when it's been closed to the tray (the
    # window subprocess is dead, so the pending-focus flag has no one to read it).
    # _open_desktop_window no-ops if a window is already up, so this is safe for
    # the window-open case too.
    try:
        web_backend.set_surface_callback(lambda: _open_desktop_window(voice=False))
    except Exception as e:
        print(f"[hearth.tray] could not register surface callback: {e}", file=sys.stderr)

    print(f"[hearth.tray] backend on {_server_url}")

    # pystray
    try:
        import pystray  # type: ignore
    except ImportError:
        print("[hearth.tray] FATAL: pystray not installed.", file=sys.stderr)
        print("[hearth.tray] Run: pip install pystray pillow", file=sys.stderr)
        return 1

    _cur_look = _look()
    img = _icon_for_look(*_cur_look)

    # Wake-word listener — optional, off by default. Fires _open_desktop_window
    # when the user says the wake word / "hey <name>" etc.
    wake_listener = None
    wake_state = {"on": False}

    def _load_wake_pref():
        # Did the user leave the tray wake word ON last time? It should survive a
        # restart so they never have to re-arm it (that re-arming was the wedge-
        # killer). Persisted in ~/Jarvis/settings.json as tray_wake_on.
        try:
            from .web import _load_settings
            return bool(_load_settings().get("tray_wake_on"))
        except Exception:
            return False

    def _save_wake_pref(on):
        try:
            from .web import _load_settings, _save_settings
            s = _load_settings()
            s["tray_wake_on"] = bool(on)
            _save_settings(s)
        except Exception:
            pass

    def _start_wake():
        nonlocal wake_listener
        try:
            from .wake import WakeListener
        except ImportError:
            return False
        if wake_listener and wake_listener.is_active():
            return True

        def on_wake(phrase: str):
            # Wake the assistant straight INTO voice mode — open the window
            # already listening with the HUD up, so you just keep talking.
            print(f"[hearth.tray] wake: '{phrase}' — opening into voice mode", flush=True)
            _open_desktop_window(voice=True)

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

    def on_voice(icon, item):
        # Open Hearth straight into voice mode (listening + HUD).
        _open_desktop_window(voice=True)

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
        # Persist ONLY on the explicit user toggle — not in _stop_wake(), which
        # also runs on Quit; otherwise quitting would silently disarm it.
        _save_wake_pref(wake_state["on"])
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
        # Kill every child we spawned (the window process, phone bridges, the
        # python/llama sentinels) so no Hearth.exe lingers holding a file lock
        # after Quit — that stray process is what blocked reinstalling/rebuilding
        # ("file is open in Hearth" even though the tray was gone). Then hard-exit
        # so THIS process dies too instead of hanging on a stuck thread.
        try:
            import psutil as _ps
            _me = _ps.Process(os.getpid())
            for _ch in _me.children(recursive=True):
                try:
                    _ch.kill()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            from .crashlog import clean_exit
            clean_exit()
        except Exception:
            pass
        os._exit(0)

    _chat_item = chat_menu_item

    def _items():
        pinned, rest = _menu_chats()
        out = [pystray.MenuItem("Open Inglen", on_open, default=True), pystray.Menu.SEPARATOR]
        if pinned:
            out.append(pystray.MenuItem("Pinned", None, enabled=False))
            out += [_chat_item(c) for c in pinned]
            out.append(pystray.Menu.SEPARATOR)
        if rest:
            out.append(pystray.MenuItem("Recent", None, enabled=False))
            out += [_chat_item(c) for c in rest[:3]]
        more = [_chat_item(c) for c in rest[3:]]
        if more:
            more.append(pystray.Menu.SEPARATOR)
        more += [pystray.MenuItem("Open in browser", on_browser),
                 pystray.MenuItem("Open workspace folder", on_workspace)]
        out.append(pystray.MenuItem("More", pystray.Menu(*more)))
        out += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("New chat", lambda icon, item: open_chat("new")),
            pystray.MenuItem("Voice mode", on_voice),
            pystray.MenuItem(lambda item: f"Listen for \"{_wake_name()}\"", on_toggle_wake,
                             checked=lambda item: wake_state["on"]),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Inglen", on_quit),
        ]
        return out

    _dpi_aware()
    _dark_menus()
    menu = pystray.Menu(lambda: _items())

    # ASCII hyphen, NOT an em-dash: X11 encodes the window title as latin-1
    # (via Xlib set_wm_name), and a U+2014 here crashes the tray on Linux
    # before it ever appears. Windows uses a different pystray backend so it
    # never surfaced there.
    icon = pystray.Icon("hearth", img, "Inglen - local AI", menu)
    # Notices from this process go through this icon instead of adding one each.
    try:
        from . import reminders as _rem
        _rem.use_tray(lambda title, body: icon.notify(body, title))
    except Exception:
        pass

    def _follow_look():
        # The window saves its theme and accent; redraw the tray icon to match.
        # The chat list in the menu is rebuilt when chats change.
        last = _cur_look
        seen = None
        while True:
            time.sleep(3)
            try:
                p, r = _menu_chats()
                sig = [(c["id"], c.get("title"), c.get("pinned")) for c in p + r]
                if sig != seen:
                    seen = sig
                    icon.update_menu()
            except Exception:
                pass
            now = _look()
            if now != last:
                last = now
                _dark_menus(now[0])
                try:
                    icon.icon = _icon_for_look(*now)
                except Exception:
                    pass
    threading.Thread(target=_follow_look, daemon=True, name="tray-look").start()
    # Auto-open the window if --open OR running as a bundled exe (so users
    # who double-click Hearth.exe get a window, not just a tray icon).
    if args.open or getattr(sys, "frozen", False):
        threading.Timer(0.5, _open_desktop_window).start()
    if args.wake or _load_wake_pref():
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
