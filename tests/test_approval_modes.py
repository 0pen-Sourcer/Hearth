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
# Handing a task to an agent program has no command to read; on Auto it stopped to ask every time.
check("Auto hands a task to an agent program without asking",
      tools.mode_allows("delegate", {"agent": "codex", "task": "write index.html"}))
# Auto asked only about deletes and moves, so a push, an install or an upload
# went out without a word. Routine work is what Auto is for.
out = ["git push origin main", "npm publish", "winget install vlc", "pip install requests",
       "curl -X POST https://example.org", "Invoke-RestMethod -Method Post -Uri https://example.org",
       "Set-ExecutionPolicy Unrestricted"]
check("Auto still asks before anything that goes out or changes the machine",
      not any(tools.mode_allows("run_command", {"command": c}) for c in out))
check("and before sending mail or a message to the phone",
      not tools.mode_allows("send_email", {}) and not tools.mode_allows("send_to_phone", {}))
check("routine commands still run, including ones that only mention those words",
      all(tools.mode_allows("run_command", {"command": c}) for c in
          ("git status", 'git commit -m "push the parser"', "npm run build", "curl https://example.org")))

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

# JARVIS asked on Auto before posting a prompt to the model server on this PC:
# a request to localhost was read as data leaving the machine.
local = ("$b=@{model='local'}; Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/chat/completions' "
         "-Method Post -Body ($b|ConvertTo-Json)")
check("on Auto, sending a request to a server on this PC runs without asking",
      tools.mode_allows("run_command", {"command": local})
      and tools.mode_allows("run_command", {"command": "curl -X POST http://localhost:8080/api -d x"}))
check("one that also names an outside address still asks",
      not tools.mode_allows("run_command", {"command": "curl -X POST http://localhost:8080 https://x.example -d x"}))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all approval mode checks passed")
