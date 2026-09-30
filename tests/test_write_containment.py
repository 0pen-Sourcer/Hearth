"""Writes stay in the workspace, judged by where they land.

The check was a text prefix on the path as written. The workspace spelled in
another case counted as outside, and a link inside the workspace pointing
elsewhere counted as inside, so a write through it landed outside with no
question asked.

Runs in a throwaway workspace with no host to ask, so anything outside is
refused.

    .\\.venv\\Scripts\\python.exe tests\\test_write_containment.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-wcont-")
OUTSIDE = tempfile.mkdtemp(prefix="hearth-outside-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
for k in ("HEARTH_AUTO_APPROVE", "JARVIS_AUTO_APPROVE", "JARVIS_EXTRA_WORKSPACES"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T                                        # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def allowed(path):
    try:
        T._resolve_write(path)
        return True
    except PermissionError:
        return False


check("a file in the workspace can be written", allowed(os.path.join(WS, "a.txt")))
check("so can the same file with the workspace in another case",
      allowed(os.path.join(WS.swapcase() if os.name == "nt" else WS, "a.txt")))
check("a folder outside cannot", not allowed(os.path.join(OUTSIDE, "a.txt")))

link = os.path.join(WS, "shortcut")
if os.name == "nt":
    made = subprocess.run(["cmd", "/c", "mklink", "/J", link, OUTSIDE],
                          capture_output=True).returncode == 0
else:
    os.symlink(OUTSIDE, link)
    made = True
if made:
    check("a link inside the workspace does not carry a write outside it",
          not allowed(os.path.join(link, "escaped.txt")))
    T.execute_tool("write_file", {"path": os.path.join(link, "escaped.txt"), "content": "x"})
    check("and write_file leaves nothing there",
          not os.path.exists(os.path.join(OUTSIDE, "escaped.txt")))
else:
    print("skip  could not make a link here")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all write containment checks passed")
