"""Bundle entrypoint for `Hearth-window.exe` — opens a PyWebView native
window pointed at the running backend URL.

The tray app (Hearth.exe) spawns this as a subprocess when the user clicks
"Open Hearth". PyWebView's `webview.start()` MUST run on the main thread,
which conflicts with pystray's blocking event loop — so we ship them as
two separate processes.

Usage (from the tray):
    Hearth-window.exe --url http://127.0.0.1:8765/
"""
from __future__ import annotations
import os
import sys
import multiprocessing

# A spawned child of a windowed build has no streams either, and it never reaches
# the redirect further down: a library printing from the speech worker would kill it.
if "--multiprocessing-fork" in sys.argv:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

# PyInstaller + multiprocessing safety (see _launch_tray): intercept a spawned
# child re-launch before anything else. No-op for a normal launch.
multiprocessing.freeze_support()

# UTF-8 out, as in _launch_tray: the packaged app ignores PYTHONIOENCODING.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Same null-stderr safety as the tray launcher
if getattr(sys, "frozen", False) and (sys.stderr is None or sys.stdout is None):
    # The workspace hearth.default_workspace() will pick: ~/Jarvis only when an older install made it.
    _home = os.path.expanduser("~")
    _ws = (os.environ.get("HEARTH_WORKSPACE") or os.environ.get("JARVIS_WORKSPACE")
           or (os.path.join(_home, "Jarvis") if os.path.isdir(os.path.join(_home, "Jarvis"))
               else os.path.join(_home, "Inglen")))
    _log_dir = os.path.join(_ws, "logs")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _f = open(os.path.join(_log_dir, "hearth_window.log"), "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = _f
        sys.stderr = _f
    except OSError:
        class _Null:
            def write(self, *_a, **_kw): return 0
            def flush(self): pass
            def isatty(self): return False
        sys.stdout = sys.stderr = _Null()

_here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if _here not in sys.path:
    sys.path.insert(0, _here)

from hearth.desktop_attach import main

if __name__ == "__main__":
    sys.exit(main())
