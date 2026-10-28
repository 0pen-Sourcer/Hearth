"""Auto mode asks before what deletes, overwrites or hides itself, in the forms
that got past it before: `cmd /c rd /s /q`, `find -delete`, an interpreter's
inline code that deletes, PowerShell's encoded commands, and writes into the
home folder's own files. Ordinary commands still run without asking.

    .\\.venv\\Scripts\\python.exe tests\\test_auto_mode_holes.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-auto-")
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = HOME
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
os.environ["HEARTH_NO_BRIDGES"] = "1"

from hearth import tools as T  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


ASK = ["cmd /c rd /s /q %USERPROFILE%\\Documents",
       "find ~/Documents -delete",
       "find . -name '*.py' -exec rm {} +",
       "python -c \"import shutil; shutil.rmtree('/home/me/Documents')\"",
       "py -c \"import os; os.remove('notes.txt')\"",
       "node -e \"require('fs').rmSync(process.env.HOME+'/Documents',{recursive:true})\"",
       "python3 -c \"open('/etc/hosts','w').write('')\"",
       "powershell -enc UgBlAG0AbwB2AGUALQBJAHQAZQBtACAALQBSAGUAYwB1AHIAcwBlACAAfgAvAEQAbwBjAHUAbQBlAG4AdABzAA==",
       "pwsh -EncodedCommand UgBlAG0AbwB2AGUALQBJAHQAZQBtAA==",
       "echo cm0gLXJmIH4= | base64 -d | sh",
       "echo hi > ~/.bashrc",
       "echo hi >> $HOME/.ssh/authorized_keys",
       "dd if=/dev/zero of=/dev/sda", "shred -u secrets.txt"]
RUN = ["python -c \"print(2 + 2)\"", "node -e \"console.log(process.version)\"", "git status", "dir",
       "find . -name '*.py'", "python script.py", "echo hi", "pip list", "npm test"]
for c in ASK:
    check(f"asks in Auto: {c[:70]}", T.needs_a_tap("run_command", {"command": c}))
for c in RUN:
    check(f"runs in Auto: {c}", not T.needs_a_tap("run_command", {"command": c}))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all Auto mode checks passed")
