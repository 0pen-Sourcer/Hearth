"""Bundle entrypoint for `Hearth.exe` (the tray launcher).

PyInstaller bundles entry-point scripts as top-level modules — they lose their
relative-import context. So we wrap the package's `tray.main()` here, after
adding the bundle directory to sys.path. Running `python _launch_tray.py`
directly also works for development.

CRITICAL for windowed PyInstaller builds: when the exe is built with
`console=False`, `sys.stdout` and `sys.stderr` are None. ANY call to print()
or sys.stderr.write() (e.g. argparse error messages) crashes with
`AttributeError: 'NoneType' has no attribute 'write'`. Redirect to a log
file BEFORE importing anything else.
"""
from __future__ import annotations
import os
import sys

# Redirect stdout/stderr to a log file when frozen + windowed.
# Open in line-buffered append mode so we don't lose late writes.
if getattr(sys, "frozen", False) and (sys.stderr is None or sys.stdout is None):
    _log_dir = os.path.join(os.path.expanduser("~"), "Jarvis", "logs")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _log_path = os.path.join(_log_dir, "hearth_tray.log")
        _f = open(_log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = _f
        sys.stderr = _f
    except OSError:
        # Last resort: swallow writes via a /dev/null-ish object
        class _Null:
            def write(self, *_a, **_kw): return 0
            def flush(self): pass
            def isatty(self): return False
        sys.stdout = sys.stderr = _Null()

# When frozen, PyInstaller sets _MEIPASS to the temp extraction dir; the
# bundled `hearth/` package lives next to this script in dist/Hearth/.
_here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if _here not in sys.path:
    sys.path.insert(0, _here)

from hearth.tray import main

if __name__ == "__main__":
    sys.exit(main())
