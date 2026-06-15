"""Bundle entrypoint for `Hearth-cli.exe`. Console mode — keeps stderr."""
from __future__ import annotations
import os
import runpy
import sys

# Frozen multi-entry: the built-in LLM server re-invokes THIS exe with a
# sentinel because sys.executable is the bundle, not python. Route it to
# llama_cpp.server's CLI before anything else.
if "--hearth-run-llama-server" in sys.argv:
    _i = sys.argv.index("--hearth-run-llama-server")
    sys.argv = [sys.argv[0]] + sys.argv[_i + 1:]
    try:
        from llama_cpp.server.__main__ import main as _llama_main
        _llama_main()
    except Exception as _e:
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
# sentinel; runpy the script with the bundled libraries (reportlab, matplotlib,
# python-pptx, ...) so the build scripts actually run in the packaged app.
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
