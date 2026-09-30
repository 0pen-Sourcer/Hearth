"""Approval modes: Ask, Auto-edit and Auto.

Ask asks before anything that acts on the machine. Auto-edit lets file edits
in the workspace through and asks about the rest. Auto runs without asking,
except commands that delete, move or stop things and the delete/move tools,
which still ask. The window, the terminal and background helpers all read the
same mode, and the old YOLO switch reads as Auto.

    .\\.venv\\Scripts\\python.exe tests\\test_approval_modes.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="hearth-modes-")
os.environ["HEARTH_WORKSPACE"] = WS
for k in ("HEARTH_AUTO_APPROVE", "JARVIS_AUTO_APPROVE"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import tools                                             # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


tools.WORKSPACE = WS


def set_settings(**kw):
    with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(kw, f)


edit = ("edit_file", {"path": "notes.txt"})
ls = ("run_command", {"command": "Get-ChildItem"})
rm = ("run_command", {"command": "Remove-Item -Recurse old"})
delete = ("delete_path", {"path": "old"})
app = ("open_app", {"name": "notepad"})

set_settings()
check("with nothing chosen the mode is Ask", tools.approval_mode() == "ask")
check("Ask lets nothing through", not any(tools.mode_allows(*c) for c in (edit, ls, rm, delete, app)))

set_settings(approval_mode="edits")
check("Auto-edit lets a file edit through", tools.mode_allows(*edit))
check("Auto-edit still asks about commands and apps", not tools.mode_allows(*ls) and not tools.mode_allows(*app))

set_settings(approval_mode="auto")
check("Auto lets an ordinary command and an app through", tools.mode_allows(*ls) and tools.mode_allows(*app))
check("Auto still asks about a destructive command", not tools.mode_allows(*rm))
check("Auto still asks about deleting", not tools.mode_allows(*delete))
check("Auto asks about a command it cannot read", not tools.mode_allows("run_command", {}))

set_settings(yolo=True)
check("the old YOLO switch reads as Auto", tools.approval_mode() == "auto")
check("and no longer lets a destructive command through unasked", not tools.mode_allows(*rm))

set_settings(approval_mode="edits")
check("a background helper gets the same answer",
      tools.standing_permission(*edit) == "allow" and tools.standing_permission(*ls) == "ask")

# The window's permission check applies the mode before prompting.
from hearth import web                                               # noqa: E402
asked = []
chk = web._make_permission_check(lambda *a, **k: asked.append(a))
web._always_allow.clear(); web._always_deny.clear()
set_settings(approval_mode="auto")
check("the window runs an allowed call without asking", chk(*ls) == "allow" and not asked)
web._always_deny.add("open_app")
check("a tool set to never still never runs, even in Auto", chk(*app) == "deny")

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all approval mode checks passed")
