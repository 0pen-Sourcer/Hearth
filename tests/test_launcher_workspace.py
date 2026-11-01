"""A launcher's log folder must not decide the workspace: on a fresh PC it may not create
~/Jarvis (hearth.default_workspace() takes an existing ~/Jarvis to mean an older install)."""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = 0


def check(what, ok):
    global fails
    print(("ok    " if ok else "FAIL  ") + what)
    fails += 0 if ok else 1


def log_dir_of(launcher, home, env):
    src = open(os.path.join(ROOT, launcher), encoding="utf-8").read()
    m = re.search(r"\n(    (?:# The workspace.*\n    )?(?:_home|_log_dir) = .*?\n    _log_dir = [^\n]*\n|    _log_dir = [^\n]*\n)", src, re.S)
    if not m:
        return None
    block = "\n".join(line[4:] for line in m.group(1).splitlines())
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "HEARTH_WORKSPACE", "JARVIS_WORKSPACE")}
    try:
        os.environ["HOME"] = os.environ["USERPROFILE"] = home
        for k in ("HEARTH_WORKSPACE", "JARVIS_WORKSPACE"):
            os.environ.pop(k, None)
        os.environ.update(env)
        scope = {"os": os}
        exec(block, scope)
        return scope["_log_dir"]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


for launcher in ("_launch_tray.py", "_launch_window.py", "_launch_cli.py"):
    fresh = tempfile.mkdtemp(prefix="inglen-fresh-")
    got = log_dir_of(launcher, fresh, {})
    check(f"{launcher}: a fresh PC logs under ~/Inglen ({got})",
          got is not None and os.path.normcase(got) == os.path.normcase(os.path.join(fresh, "Inglen", "logs")))

    older = tempfile.mkdtemp(prefix="inglen-older-")
    os.makedirs(os.path.join(older, "Jarvis"))
    got = log_dir_of(launcher, older, {})
    check(f"{launcher}: an older install keeps ~/Jarvis ({got})",
          got is not None and os.path.normcase(got) == os.path.normcase(os.path.join(older, "Jarvis", "logs")))

    moved = tempfile.mkdtemp(prefix="inglen-moved-")
    got = log_dir_of(launcher, moved, {"HEARTH_WORKSPACE": os.path.join(moved, "D-Inglen")})
    check(f"{launcher}: a moved workspace logs inside it ({got})",
          got is not None and os.path.normcase(got) == os.path.normcase(os.path.join(moved, "D-Inglen", "logs")))

print(f"\n{fails} check(s) failed" if fails else "\nall launcher workspace checks passed")
sys.exit(1 if fails else 0)
