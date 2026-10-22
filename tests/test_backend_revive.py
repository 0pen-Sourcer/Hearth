"""The window starts Hearth's backend again when it stops.

The window outlives the process that serves it; when native code took that
process down, the window showed "Failed to fetch" until Hearth was restarted
by hand. It restarts it only when the backend doesn't answer and its process
is gone (a busy backend is left alone), says so, reloads the page, and stops
trying after a few times.

    .\\.venv\\Scripts\\python.exe tests\\test_backend_revive.py
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import desktop_attach as D  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


class Win:
    def __init__(self):
        self.loaded, self.js = [], []

    def load_url(self, url):
        self.loaded.append(url)

    def evaluate_js(self, js):
        self.js.append(js)


def run(states, pid_alive, limit=3, from_tray=True):
    """Drive the watcher through a list of answers (True = the backend answers)."""
    feed = list(states)
    launched = []
    os.environ.pop("HEARTH_BACKEND_CMD", None)
    if from_tray:
        os.environ["HEARTH_BACKEND_CMD"] = json.dumps(["Hearth.exe"])
    os.environ["HEARTH_BACKEND_PID"] = "4242"
    D._alive = lambda pid: pid_alive
    win = Win()
    t = D.revive_backend(win, "http://127.0.0.1:8765/", every=0, misses=3, limit=limit,
                         answers=lambda url: feed.pop(0) if feed else True,
                         launch=lambda cmd: launched.append(cmd) or True,
                         sleep=lambda s: (time.sleep(0.001) if feed else (_ for _ in ()).throw(SystemExit)))
    t.join(5)
    return win, launched


win, launched = run([True, False, False, False, False, True], pid_alive=False)
check("a backend that is gone is started again, the way the tray started it", launched == [["Hearth.exe"]])
check("the window says so", any("stopped unexpectedly" in j for j in win.js))
check("and reloads the page once it answers", win.loaded == ["http://127.0.0.1:8765/"])

win, launched = run([False, False, False, False, False, False], pid_alive=True)
check("a backend that is only slow to answer (its process alive) is left alone", launched == [] and win.loaded == [])

win, launched = run([True, False, True, False, True], pid_alive=False)
check("one missed answer isn't a stop", launched == [])

win, launched = run([False] * 400, pid_alive=False, limit=2)
check(f"it gives up after a few tries rather than looping ({len(launched)})", len(launched) == 2)

win, launched = run([False] * 6, pid_alive=False, from_tray=False)
check("a window not started by the tray never starts one", launched == [])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all backend revive checks passed")
