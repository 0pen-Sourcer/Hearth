"""The desktop's signs switch one by one; an app it opened is gone back to; a worker
spawned by a windowed build has streams.

    .\\.venv\\Scripts\\python.exe tests\\test_desktop_signs.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
os.environ.setdefault("HEARTH_NO_ACTIVITY_LOG", "1")
import _home  # noqa: E402,F401

from hearth import activity_hud as hud, desktop_session as ds, tools as T, web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# ------------------------------------------------------------ one switch each
keep = (hud.desktop_here, web._load_settings)
hud.desktop_here = lambda: True
try:
    web._load_settings = lambda: {}
    check("all on by default", ds._enabled("cu_glow") and ds._enabled("cu_panel") and ds._enabled("cu_pointer"))
    web._load_settings = lambda: {"cu_glow": False}
    check("the glow goes off alone", not ds._enabled("cu_glow") and ds._enabled("cu_panel"))
    web._load_settings = lambda: {"cu_panel": False}
    check("the panel goes off alone", not ds._enabled("cu_panel") and ds._enabled("cu_glow"))
    web._load_settings = lambda: {"activity_hud": False}
    check("the older switch still turns them off", not ds._enabled("cu_glow") and not ds._enabled("cu_panel"))
    web._load_settings = lambda: {"activity_hud": False, "cu_panel": True}
    check("until one is set", ds._enabled("cu_panel"))
finally:
    hud.desktop_here, web._load_settings = keep

html = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("Settings has a switch for each",
      all(f'data-key="{k}"' in html for k in ("activity_hud", "cu_panel", "cu_glow", "cu_pointer")))

# ------------------------------------------------------- going back to an app
if sys.platform == "win32":
    keep = (T._snapshot_windows, T._bring_newest_to_front, T._record_focus_target, ds.bring_to_front, T.open_outside)
    import ctypes
    live = ctypes.windll.user32.GetDesktopWindow()      # a window that exists
    raised, launched = [], []
    ds.bring_to_front = lambda h: raised.append(h) or True
    T._record_focus_target = lambda h=None: None
    T._OPENED.clear()
    T._OPENED["notepad"] = live
    try:
        out = T._open_app({"name": "Notepad"})
        check("an app this task opened comes back instead of opening again",
              raised == [live] and "already open" in out)
        T._OPENED["notepad"] = 0
        check("a window that's gone doesn't count", not T._back_to_opened("notepad"))
        T._OPENED.clear()
        check("nor does a window this task never opened", not T._back_to_opened("notepad"))
    finally:
        T._snapshot_windows, T._bring_newest_to_front, T._record_focus_target, ds.bring_to_front, T.open_outside = keep
        T._OPENED.clear()

# ----------------------------------------------------- a worker's missing streams
for launcher in ("_launch_tray.py", "_launch_window.py"):
    src = open(os.path.join(ROOT, launcher), encoding="utf-8").read()
    head = src[:src.index("multiprocessing.freeze_support()")]
    with tempfile.TemporaryDirectory() as d:
        out_file = os.path.join(d, "out.json")
        body = ("import sys, json\nsys.stdout = sys.stderr = None\n"
                "sys.argv = ['Hearth.exe', '--multiprocessing-fork', 'parent_pid=1']\n"
                f"exec(compile({head!r}, 'launcher', 'exec'))\n"
                f"json.dump([sys.stdout is None, sys.stderr is None], open({out_file!r}, 'w'))\n")
        script = os.path.join(d, "t.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(body)
        subprocess.run([sys.executable, script], capture_output=True, timeout=30)
        got = json.load(open(out_file)) if os.path.exists(out_file) else None
    check(f"{launcher}: a spawned worker has streams before it runs", got == [False, False])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all desktop sign checks passed")
