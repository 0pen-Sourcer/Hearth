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

# Frozen multi-entry: the built-in LLM server can't be launched as
# `python -m llama_cpp.server` because in the bundle sys.executable is THIS
# exe (entrypoint = tray), not a python interpreter. llmserver re-invokes the
# exe with this sentinel; hand off to llama_cpp.server's CLI before anything
# else (the parent already pipes our stdout/stderr to llamaserver.log).
if "--hearth-run-llama-server" in sys.argv:
    _i = sys.argv.index("--hearth-run-llama-server")
    sys.argv = [sys.argv[0]] + sys.argv[_i + 1:]
    try:
        from llama_cpp.server.__main__ import main as _llama_main
        _llama_main()
    except Exception as _e:
        # Don't pop a crash dialog — the parent reads stderr (→ llamaserver.log)
        # and surfaces a clean "built-in server unavailable" message. Happens on
        # a LITE build (llama_cpp not bundled) or a missing llama.dll.
        try:
            (sys.stderr or sys.__stdout__).write(
                f"[hearth] built-in LLM server unavailable in this build "
                f"(llama_cpp failed to load): {_e}\n")
        except Exception:
            pass
        raise SystemExit(1)
    raise SystemExit(0)

# Frozen python execution: the skills run `python build.py`, but sys.executable
# is THIS exe, not a python interpreter. The rewriter re-invokes us with this
# sentinel; runpy the script with the bundled libraries so the build scripts
# actually run in the packaged app.
if "--hearth-run-python" in sys.argv:
    _i = sys.argv.index("--hearth-run-python")
    _rest = sys.argv[_i + 1:]
    import runpy
    if _rest and _rest[0] == "-c":
        _code = _rest[1] if len(_rest) > 1 else ""
        sys.argv = ["-c"] + _rest[2:]
        exec(compile(_code, "<string>", "exec"), {"__name__": "__main__"})
    elif _rest:
        sys.argv = list(_rest)
        runpy.run_path(_rest[0], run_name="__main__")
    raise SystemExit(0)

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
