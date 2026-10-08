"""The activity log behind the Logs page says which calls failed and what came back.

A tool that fails answers with text starting "Error" (or JSON with ok false),
and the log wrote that down as a success with only its length, so the Errors
filter showed nothing and the page couldn't say what a call returned. The
line written each time Hearth's tools start for an agent brain also filled the
page's window, hiding the calls.

    .\\.venv\\Scripts\\python.exe tests\\test_activity_log.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-actlog-")
os.environ["JARVIS_WORKSPACE"] = WS
os.environ.pop("HEARTH_NO_ACTIVITY_LOG", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools as T, web                                   # noqa: E402

if not os.path.abspath(T.ACTIVITY_LOG).lower().startswith(os.path.abspath(WS).lower()):
    sys.exit(f"refusing to run: the log resolved to {T.ACTIVITY_LOG}")

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


note = os.path.join(WS, "note.txt")
with open(note, "w", encoding="utf-8") as f:
    f.write("hello there")
T.execute_tool("read_file", {"path": note})
T.execute_tool("list_directory", {"path": os.path.join(WS, "no-such-folder")})
with open(T.ACTIVITY_LOG, "a", encoding="utf-8") as f:
    for _ in range(50):
        f.write(json.dumps({"ts": "2026-10-08T12:00:00", "event": "server_start"}) + "\n")

events = web._tail_log(4)
results = [e for e in events if e.get("event") == "result"]
check(f"the tool-server start lines don't push the calls out of the window {[e['event'] for e in events][:4]}",
      len([e for e in events if e.get("event") != "server_start"]) == 4)
good, bad = (results + [{}, {}])[:2]
check(f"a call that worked is logged as working, with what came back {good}",
      good.get("ok") is True and "hello there" in good.get("preview", ""))
check(f"a call that failed is logged as failed, with what it said {bad}",
      bad.get("ok") is False and bad.get("preview", "").startswith("Error"))
check("JSON with ok false counts as failed; other text doesn't",
      T._says_failed('{"ok": false, "error": "no"}') and not T._says_failed('{"ok": true}')
      and not T._says_failed("No errors found"))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all activity log checks passed")
