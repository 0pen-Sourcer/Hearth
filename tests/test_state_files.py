"""Inglen's own records in the workspace can't be rewritten by a free file edit.

- The undo journal: an Undo puts back or removes whatever path an entry names, and
  pruning removes the folder an old entry's copy sits in. A file edit landing in
  .undo asks in every mode, and an entry whose copy isn't one Inglen kept is never
  followed, by Undo or by pruning.
- The pause set when the person takes the mouse back (takeover.json), and the
  window's address a terminal sends its turns to (window.json), ask the same way.

Temp home and workspace.

    python tests/test_state_files.py
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402

WS, HOME = _home.WORKSPACE, _home.HOME
os.environ.update(HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1", HEARTH_NO_TAKEOVER="1")
for k in ("HEARTH_AUTO_APPROVE", "JARVIS_AUTO_APPROVE"):
    os.environ.pop(k, None)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def settings(**kw):
    s = {"onboarded": True, "tour_done": True, "auto_reviewer": "off"}
    s.update(kw)
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(s, f)


settings(approval_mode="edits")
from hearth import tools, undo  # noqa: E402


def folder(name):
    p = os.path.join(HOME, "Documents", name)
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "chapter1.docx"), "w") as f:
        f.write("years of work")
    return p


for mode in ("edits", "auto"):
    settings(approval_mode=mode)
    for p in (".undo/journal.json", ".UNDO/files/x/y.txt", "takeover.json", "window.json"):
        check(f"in {mode}, writing {p} asks", not tools.mode_allows("write_file", {"path": p, "content": "{}"}))
    check(f"in {mode}, moving a file onto the journal asks",
          not tools.mode_allows("move_path", {"source": "a.json", "destination": ".undo/journal.json"}))
    check(f"in {mode}, an ordinary file still goes", tools.mode_allows("write_file", {"path": "notes/plan.md", "content": "x"}))

# The records themselves, written as a script or another program could: never followed outside .undo.
settings(approval_mode="edits")
victim = folder("thesis")
os.makedirs(os.path.join(WS, ".undo"), exist_ok=True)
with open(os.path.join(WS, ".undo", "journal.json"), "w", encoding="utf-8") as f:
    json.dump({"changes": [{"id": "old1", "at": 0, "chat": "", "items": [
        {"kind": "change", "path": os.path.join(WS, "x.txt"), "existed": True,
         "backup": os.path.join(victim, "chapter1.docx")}]}], "created": {}}, f)
tools.execute_tool("write_file", {"path": "notes.txt", "content": "hi"})
check("pruning an old entry never removes a folder its copy wasn't kept in", os.path.exists(victim))

startup = os.path.join(HOME, "Startup")
with open(os.path.join(WS, "payload.txt"), "w") as f:
    f.write("calc")
d = undo._read()
d["changes"].append({"id": "fake2", "at": time.time(), "chat": "", "items": [
    {"kind": "change", "path": os.path.join(startup, "update.bat"), "existed": True,
     "backup": os.path.join(WS, "payload.txt")}]})
undo._write(d)
r = undo.undo("fake2")
check(f"Undo won't put back a copy Inglen didn't keep ({r})",
      not r.get("ok") and not os.path.exists(os.path.join(startup, "update.bat")))

# A real change still undoes.
tools.execute_tool("write_file", {"path": "draft.txt", "content": "one"})
tools.execute_tool("write_file", {"path": "draft.txt", "content": "two"})
last = undo.recent(1)[0]
r = undo.undo(last["id"])
with open(os.path.join(WS, "draft.txt"), encoding="utf-8") as f:
    back = f.read()
check(f"a real change still undoes ({r}, {back!r})", r.get("ok") and back == "one")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all state-file checks passed")
