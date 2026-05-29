"""Bundle entrypoint for `Hearth-cli.exe`. Console mode — keeps stderr."""
from __future__ import annotations
import os
import runpy
import sys

# Defensive null-stderr safety (only used if PyInstaller built with no console)
if getattr(sys, "frozen", False) and (sys.stderr is None or sys.stdout is None):
    _log_dir = os.path.join(os.path.expanduser("~"), "Jarvis", "logs")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _f = open(os.path.join(_log_dir, "hearth_cli.log"), "a", encoding="utf-8", buffering=1)
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

# hearth_cli.py has no main() — all its logic lives under
# `if __name__ == "__main__":`. Use runpy so that block actually fires.
if __name__ == "__main__":
    runpy.run_module("hearth_cli", run_name="__main__", alter_sys=True)
