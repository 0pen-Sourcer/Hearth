"""Ask, Auto-edit and Auto mean the same on every brain.

A local or cloud model, Claude Code and Codex each reach the window's one
permission check by their own route: the engine with Hearth's tool names,
Claude Code with its own (Write, Edit, Bash) through decide(), Codex with
command and file-change requests through ask_hearth(). Each route is driven
here with the same requests on each mode; every prompt the window would show
is recorded and answered no.

    .\\.venv\\Scripts\\python.exe tests\\test_approval_brains.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-apprbrains-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
sys.path.insert(0, ROOT)

from hearth import web, tools, claude_brain, codex_brain  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


asked: list = []


def emit(kind, **kw):
    if kind == "permission_request":
        asked.append(kw.get("name"))
        web._permission_queues[kw["id"]].put("deny")


window_check = web._make_permission_check(emit)
web._always_allow.clear()
web._always_deny.clear()
f = os.path.join(WS, "notes.txt")

# What each brain sends for: a file written in the workspace, a harmless
# command, and a delete.
ROUTES = {
    "engine (local or cloud)": {
        "write": lambda: window_check("write_file", {"path": f, "content": "x"}) == "allow",
        "command": lambda: window_check("run_command", {"command": "Get-ChildItem"}) == "allow",
        "delete": lambda: window_check("run_command", {"command": f"Remove-Item {f}"}) == "allow",
    },
    "Claude Code": {
        "write": lambda: claude_brain.decide(claude_brain._TOKEN, "Write", {"file_path": f, "content": "x"})["behavior"] == "allow",
        "command": lambda: claude_brain.decide(claude_brain._TOKEN, "Bash", {"command": "Get-ChildItem"})["behavior"] == "allow",
        "delete": lambda: claude_brain.decide(claude_brain._TOKEN, "Bash", {"command": f"Remove-Item {f}"})["behavior"] == "allow",
    },
    "Codex": {
        "write": lambda: asyncio.run(claude_brain.ask_hearth(window_check, *codex_brain._change_call({"kind": "add", "path": f, "diff": "+x"}))),
        "command": lambda: asyncio.run(claude_brain.ask_hearth(window_check, "run_command", {"command": "Get-ChildItem"})),
        "delete": lambda: asyncio.run(claude_brain.ask_hearth(window_check, *codex_brain._change_call({"kind": "delete", "path": f}))),
    },
}
claude_brain._checks[claude_brain._TOKEN] = window_check

# mode -> what may go ahead without a question
EXPECT = {
    "ask":   {"write": False, "command": False, "delete": False},
    "edits": {"write": True,  "command": False, "delete": False},
    "auto":  {"write": True,  "command": True,  "delete": False},
}

for mode, want in EXPECT.items():
    with open(os.path.join(WS, "settings.json"), "w") as fh:
        json.dump({"onboarded": True, "approval_mode": mode}, fh)
    web._load_settings()
    check(f"the mode in use is {mode}", tools.approval_mode() == mode)
    for brain, calls in ROUTES.items():
        got = {}
        for what, call in calls.items():
            asked.clear()
            ran = bool(call())
            got[what] = (ran, bool(asked))
        bad = {k: v for k, v in got.items() if v != (want[k], not want[k])}
        check(f"{mode}: {brain} runs and asks as the mode says {bad or ''}", not bad)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all approval checks passed")
