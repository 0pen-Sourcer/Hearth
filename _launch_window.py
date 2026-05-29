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

# Same null-stderr safety as the tray launcher
if getattr(sys, "frozen", False) and (sys.stderr is None or sys.stdout is None):
    _log_dir = os.path.join(os.path.expanduser("~"), "Jarvis", "logs")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _f = open(os.path.join(_log_dir, "hearth_window.log"), "a", encoding="utf-8", buffering=1)
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
