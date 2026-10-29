"""Deleting a memory in the window deletes it, and a delete that fails says so.

The owner deleted a memory, saw "Deleted", and it stayed: the window's
endpoint called memory_forget with `name` after the tool's argument check
began to require `title`, and it never read the answer.

    .\\.venv\\Scripts\\python.exe tests\\test_memory_delete.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-memdel-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
WS = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"
os.makedirs(os.path.join(WS, "memory"))

from hearth import web  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


MEM = os.path.join(WS, "memory", "timelines_of_history_ch1.md")
with open(MEM, "w", encoding="utf-8") as f:
    f.write("---\nname: timelines_of_history_ch1\ndescription: Front matter and Human Origins\ntype: reference\n---\nNotes.\n")

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
srv = web._QuietHTTPServer(("127.0.0.1", port), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def delete(name):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/memory/{name}", method="DELETE",
                                 headers={"Host": f"127.0.0.1:{port}"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def names():
    return [m["name"] for m in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/memory", timeout=20))["memories"]]


check("the memory is listed", "timelines_of_history_ch1" in names())
r = delete("timelines_of_history_ch1")
check(f"deleting it says it did ({r})", r.get("ok") is True)
check("and it's gone from the list and the disk", "timelines_of_history_ch1" not in names() and not os.path.exists(MEM))
r = delete("no_such_memory")
check(f"one that isn't there isn't reported deleted ({r})", r.get("ok") is False or "no such" in json.dumps(r).lower()
      or "not found" in json.dumps(r).lower() or "nothing" in json.dumps(r).lower())
srv.shutdown()
print("\n" + ("all memory delete checks passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
