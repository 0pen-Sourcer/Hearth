"""A phone bridge ends when the Hearth that started it is gone.

Before, a bridge kept running after its Hearth exited, and the next Hearth
started another on the same bot, so each message got two replies."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-follow-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
child = subprocess.Popen([sys.executable, "-c",
                          "from hearth.bridge_status import follow_parent; follow_parent(0.3); import time; time.sleep(120)"],
                         cwd=ROOT, env=dict(os.environ, HEARTH_PARENT_PID=str(parent.pid)))
time.sleep(2)
check("while its Hearth runs, the bridge runs", child.poll() is None)
parent.kill()
parent.wait()
for _ in range(40):
    if child.poll() is not None:
        break
    time.sleep(0.25)
check("when its Hearth is gone, the bridge ends", child.poll() is not None)
if child.poll() is None:
    child.kill()

from hearth import web                       # noqa: E402
seen = {}
real = subprocess.Popen


class Fake:
    def __init__(self, args, **kw):
        seen.update(kw.get("env") or {})

    def poll(self):
        return None


web.subprocess.Popen = Fake
web._BRIDGE_PROCS.pop("telegram", None)
web.time.sleep = lambda s: None
web._spawn_bridge("telegram")
web.subprocess.Popen = real
check("a bridge is told which Hearth started it", seen.get("HEARTH_PARENT_PID") == str(os.getpid()))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all bridge follow checks passed")
