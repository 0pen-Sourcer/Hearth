"""A bridge started from the window keeps running however much it prints.

Its output went to a pipe that nothing read once the bridge was up. A bridge
prints a line for each message it handles, so after a few kilobytes the pipe
was full and the next print blocked for good: the bridge still showed as
running and never answered again. The output now goes to the bridge's log,
the file the log view reads, and nothing else did write telegram's or
whatsapp's.

    .\\.venv\\Scripts\\python.exe tests\\test_bridge_spawn.py
"""
from __future__ import annotations

import _home  # noqa: F401,E402  temporary home and workspace

import os
import sys
import tempfile
import time

WS = tempfile.mkdtemp(prefix="hearth-bridge-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import web                                               # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


web._BRIDGE_MODULES["standin"] = "_standin_bridge"
os.environ["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
marker = os.path.join(WS, "reached.txt")
os.environ["HEARTH_STANDIN_MARKER"] = marker

# -------------------------------------------------------- a chatty bridge
running, err = web._spawn_bridge("standin")
check("the bridge starts", running and err is None)
deadline = time.time() + 20
while time.time() < deadline and not os.path.exists(marker):
    time.sleep(0.2)
check("after printing 256 KB it is still working, not blocked on a full pipe",
      os.path.exists(marker))
log = os.path.join(WS, "logs", "standin_bridge.log")
text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
check("what it printed is in its log, the file the log view reads",
      "message 04095" in text)
check("and a dash it printed reads as a dash, not a broken character",
      "bridge up \u2014 long-polling" in text)
web._BRIDGE_PROCS["standin"].kill()
web._BRIDGE_PROCS["standin"].wait()

# ------------------------------------------------------ one that dies at once
os.environ["HEARTH_STANDIN_MODE"] = "die"
running, err = web._spawn_bridge("standin")
check(f"a bridge that dies on start is reported with its reason: {err!r}",
      not running and err and "missing a Python package" in err)
os.environ.pop("HEARTH_STANDIN_MODE")

# ----------------------------------------------------------- the log stays small
with open(log, "ab") as f:
    f.write(b"old line\n" * 200_000)
web._bridge_log_path("standin")
check("an old log is trimmed to its newest part when the bridge starts again",
      os.path.getsize(log) <= web._BRIDGE_LOG_KEEP)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all bridge spawn checks passed")
