"""The edge glow's window is built without a native fault: faulthandler logs
each access violation Windows raises, even one it handles, and the crash notice
reads that log. The window is built and painted but never shown.

    python tests/test_glow_build.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


if sys.platform != "win32":
    print("skipped: Windows only")
    sys.exit(0)

LOG = os.path.join(tempfile.mkdtemp(prefix="inglen-glow-"), "fault.log")
CHILD = f"""import sys, faulthandler, threading, ctypes
sys.path.insert(0, {os.path.join(ROOT, 'tests')!r}); sys.path.insert(0, {ROOT!r})
import _home
f = open({LOG!r}, "w")
faulthandler.enable(file=f)
from hearth import desktop_session as ds
before = ctypes.windll.user32.UpdateLayeredWindow.argtypes
out = {{}}
def go():
    out["h"] = ds._Glow._build()
    if out["h"]:
        ds._Glow._paint((224, 123, 76))
        ds._Glow._set_alpha(0)
        out["again"] = ds._Glow._build()
t = threading.Thread(target=go); t.start(); t.join(10)
print("hwnd", bool(out.get("h")), bool(out.get("again")))
print("shared", ctypes.windll.user32.UpdateLayeredWindow.argtypes is before)
"""
r = subprocess.run([sys.executable, "-X", "utf8", "-c", CHILD], capture_output=True, text=True, timeout=60)
with open(LOG, encoding="utf-8", errors="replace") as f:
    log = f.read()
check(f"the window is built, painted and built again ({r.stdout.strip()!r})",
      "hwnd True True" in r.stdout)
check(f"without an access violation ({log.count('access violation')} logged)", "access violation" not in log)
check("and the shared ctypes.windll is left as it was", "shared True" in r.stdout)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all glow build checks passed")
