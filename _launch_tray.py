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
    # Frozen-app DLL path. llama.dll -> ggml-cuda.dll -> cudart/cublas, but
    # PyInstaller bundles the CUDA runtime under _MEIPASS/nvidia/*/bin (not next
    # to llama.dll), so ctypes can't resolve the transitive deps and llama.dll
    # fails to load. Put the bundled CUDA + llama lib dirs on the DLL search
    # path before importing llama_cpp. (From source, the system PATH handles
    # this; this block is a no-op there since _MEIPASS/nvidia won't exist.)
    try:
        _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        _dll_dirs = [os.path.join(_base, "llama_cpp", "lib")]
        _nv = os.path.join(_base, "nvidia")
        if os.path.isdir(_nv):
            for _s in os.listdir(_nv):
                _b = os.path.join(_nv, _s, "bin")
                if os.path.isdir(_b):
                    _dll_dirs.append(_b)
        for _d in _dll_dirs:
            if os.path.isdir(_d):
                try:
                    os.add_dll_directory(_d)
                except Exception:
                    pass
                if _d not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass
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
    try:
        if _rest and _rest[0] == "-c":
            _code = _rest[1] if len(_rest) > 1 else ""
            sys.argv = ["-c"] + _rest[2:]
            exec(compile(_code, "<string>", "exec"), {"__name__": "__main__"})
        elif _rest and _rest[0] == "-m":
            # `-m <module>` — used by the phone-bridge spawns
            # (hearth.discord_bridge / telegram_bridge / whatsapp_bridge).
            # MUST use run_module, not run_path, or runpy tries to open a file
            # literally named "-m" and dies with "can't find __main__ module".
            _mod = _rest[1] if len(_rest) > 1 else ""
            sys.argv = [_mod] + _rest[2:]
            runpy.run_module(_mod, run_name="__main__", alter_sys=True)
        elif _rest:
            sys.argv = list(_rest)
            runpy.run_path(_rest[0], run_name="__main__")
    except SystemExit:
        raise
    except BaseException as _e:
        # Never pop a Windows "unhandled exception" dialog — that dialog also
        # keeps the process alive, which makes the parent's poll() think the
        # bridge is "online" when it actually crashed. Log + exit non-zero so
        # the GUI reports the failure honestly.
        try:
            (sys.stderr or sys.__stdout__).write(f"[hearth] --hearth-run-python failed: {type(_e).__name__}: {_e}\n")
        except Exception:
            pass
        raise SystemExit(1)
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
