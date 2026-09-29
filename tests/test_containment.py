"""Workspace containment is judged by folder, not by string prefix.

With read lockdown on, a sibling folder whose name merely STARTS with the
workspace name passed the check, because the test was a text prefix. The lock
was enabled for the reader and did nothing against that case.

    .\.venv\Scripts\python.exe tests\test_containment.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["JARVIS_LOCKDOWN"] = "1"

from hearth import tools as T                                       # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ws = T.WORKSPACE.rstrip(os.sep)
check("the workspace itself is inside", T.path_within(ws, ws))
check("a file in it is inside", T.path_within(os.path.join(ws, "notes.txt"), ws))
check("a nested file is inside", T.path_within(os.path.join(ws, "a", "b.txt"), ws))
check("a sibling that starts with the same name is outside",
      not T.path_within(ws + "Evil" + os.sep + "secret.txt", ws))
check("climbing out with .. is outside",
      not T.path_within(os.path.join(ws, "..", os.path.basename(ws) + "Evil", "s"), ws))
check("the parent folder is outside", not T.path_within(os.path.dirname(ws), ws))
check("case differences do not matter on Windows" if os.name == "nt" else "case is compared as the OS does",
      T.path_within(ws.upper() + os.sep + "x.txt", ws) == (os.name == "nt"))


def refused(path):
    try:
        T._resolve_read(path)
        return False
    except PermissionError:
        return True


check("with lockdown on, a read inside the workspace is allowed",
      not refused(os.path.join(ws, "notes.txt")))
check("with lockdown on, the prefix-named sibling is refused",
      refused(ws + "Evil" + os.sep + "secret.txt"))

# web_fetch took file:// URLs and read them with no path check at all.
import tempfile                                                      # noqa: E402
_outside = os.path.join(tempfile.mkdtemp(prefix="hearth-outside-"), "secret.txt")
with open(_outside, "w", encoding="utf-8") as _f:
    _f.write("SECRET-OUTSIDE-WORKSPACE")
_url = "file:///" + _outside.replace(os.sep, "/")
check("web_fetch does not read a file outside the workspace through file://",
      "SECRET-OUTSIDE-WORKSPACE" not in T.execute_tool("web_fetch", {"url": _url}))
check("nor through a bare path",
      "SECRET-OUTSIDE-WORKSPACE" not in T.execute_tool("web_fetch", {"url": _outside}))
check("and points at read_file instead",
      "read_file" in T.execute_tool("web_fetch", {"url": _url}))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all containment checks passed")
