"""A step aimed at the folder Inglen itself is installed in asks, in every mode.

In Auto a brain ran `start chrome.exe --user-data-dir="<Inglen's install folder>"`
without asking, and Chrome wrote a whole browser profile into the app's own
folder. A command that names that folder (however it spells it), or a file
change inside it, now asks whatever the approval mode, on every brain. A
temporary folder stands in for the install; nothing runs.

    .\\.venv\\Scripts\\python.exe tests\\test_install_folder.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _home  # noqa: E402
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
with open(os.path.join(_home.WORKSPACE, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"approval_mode": "auto"}, f)

LAD = tempfile.mkdtemp(prefix="inglen-lad-")
APP = os.path.join(LAD, "Hearth")
os.makedirs(APP)
os.makedirs(APP + "Models")                          # a neighbour whose name starts the same
os.environ["HEARTH_INSTALL_DIR"] = APP
os.environ["FAKE_LAD"] = LAD

from hearth import tools as T                                         # noqa: E402

T._installed_at = []            # only the stand-in: never this PC's real install
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def asks(name, args):
    return not T.mode_allows(name, args)


for cmd in [f'start chrome.exe --user-data-dir="{APP}"',
            'start chrome.exe --user-data-dir="%FAKE_LAD%\\Hearth"',
            'Start-Process chrome -ArgumentList "--user-data-dir=$env:FAKE_LAD\\Hearth"',
            f'Get-ChildItem "{APP.replace(chr(92), "/")}/_internal" -Recurse',
            f'Remove-Item "{APP}\\Default" -Recurse']:
    check(f"asks in Auto: {cmd[:70]}", asks("run_command", {"command": cmd}))
check("a spoken yes can't allow it (needs a tap)",
      T.needs_a_tap("run_command", {"command": f'start chrome.exe --user-data-dir="{APP}"'}))
check("and says why", "installed in" in T.sends_out("run_command", {"command": f'cd "{APP}"; dir'}))
check("a command run inside it asks too", asks("run_command", {"command": "dir", "cwd": APP}))
check("a file written there asks", asks("write_file", {"path": os.path.join(APP, "Local State"), "content": "x"}))
os.environ["HEARTH_AUTO_APPROVE"] = "1"
check("auto-approve with nobody watching doesn't cover it",
      T.standing_permission("run_command", {"command": f'start chrome.exe --user-data-dir="{APP}"'}) == "ask")
os.environ.pop("HEARTH_AUTO_APPROVE")

check("a folder that only starts with the same name doesn't ask",
      not asks("run_command", {"command": f'Get-ChildItem "{APP}Models"'}))
check("an ordinary command still runs in Auto", not asks("run_command", {"command": "Get-ChildItem C:\\Users"}))

from hearth import claude_brain as CB                                 # noqa: E402
rules = CB.claude_settings()["permissions"]["ask"]
check(f"Claude Code's own edits there come back to Inglen to ask ({[r for r in rules if 'Hearth' in r][:1]})",
      any("hearth/**" in r.lower() and "inglen-lad-" in r.lower() for r in rules))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all install folder checks passed")
