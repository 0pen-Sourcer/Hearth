"""Coverage checks for the destructive-command guard.

The guard is a stop on the shortcut, not a security boundary. An approved call
runs regardless, and the approval prompt is where the real decision is made.
What it has to do is catch the ways the same action is ORDINARILY written,
because a spelling that slips through is a prompt nobody ever sees. PowerShell
gives every cmdlet a short alias and a .NET equivalent, so matching only the
long name covered one spelling in nineteen.

The second list matters as much as the first. A guard that refuses
`git commit -m "move the parser"` gets worked around, and then it guards
nothing.

    .\\.venv\\Scripts\\python.exe tests\\test_destructive_guard.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth.tools import _is_destructive                            # noqa: E402

FAILS: list = []

MUST_STOP = [
    (r"Remove-Item C:\x\y -Recurse", "the long name"),
    (r"ri C:\x\y -Recurse -Force", "its alias"),
    (r"Remove-ItemProperty HKCU:\S -Name N", "the longer name it is a prefix of"),
    (r"rd /q /s C:\x", "flags in the other order"),
    (r"rmdir /s C:\x", "the other spelling"),
    (r"[System.IO.File]::Delete('C:\x\y')", ".NET, no cmdlet name at all"),
    (r"[IO.Directory]::Delete('C:\x', $true)", ".NET without the namespace"),
    (r"[IO.File]::WriteAllText('C:\x\a.txt', 'x')", ".NET overwrite"),
    (r"Rename-Item C:\x\a.txt b.txt", "rename"),
    (r"rni C:\x\a.txt b.txt", "rename by alias"),
    (r"mi C:\a C:\b", "move by alias"),
    (r"cpi C:\a C:\b", "copy by alias"),
    (r"clc C:\x\a.txt", "truncate by alias"),
    (r"spps -Name notepad", "kill by alias"),
    (r"New-Item -Force C:\x\a.txt", "a create that truncates"),
    (r"Stop-Computer -Force", "power off"),
    (r"Format-Volume -DriveLetter D", "format, the cmdlet"),
    (r"Clear-Disk -Number 1 -RemoveData", "wipe a disk"),
    (r"Remove-Partition -DiskNumber 1 -PartitionNumber 2", "drop a partition"),
    (r"wmic process where name='x' delete", "kill through wmic"),
    (r"echo hi > ..\..\Windows\x.txt", "a redirect that climbs out"),
    (r"echo hi > C:\Windows\x.txt", "a redirect to an absolute path"),
    (r"Set-ItemProperty HKLM:\S -Name N -Value 1", "a registry write"),
    (r"Get-ChildItem C:\x | rm -Recurse", "an alias after a pipe"),
    (r"cd C:\x; del *.log", "an alias after a separator"),
    (r"taskkill /F /IM x.exe", "the plain one"),
]

# Ordinary work. A guard that fires on these gets routed around.
MUST_PASS = [
    "git status",
    "git log --oneline",
    "git commit -m 'move the parser'",
    "git commit -m 'kill the flaky test'",
    "git commit -m 'del the stale row and copy the rest'",
    "python -m pytest",
    "npm run build",
    "pip install requests",
    "docker ps",
    "gh pr list",
    "nvidia-smi",
    "echo hello",
    "rm --help",
    r"dir C:\x",
    r"Get-ChildItem C:\x",
    r"Get-Content C:\x\a.txt",
    r"New-Item -ItemType Directory C:\x\d",
    "Get-Process | Select-Object -First 5",
    "Select-String -Pattern move -Path a.txt",
    "curl https://example.com",
    "cat notes.txt > summary.txt",
]

for _cmd, _why in MUST_STOP:
    _hit = _is_destructive(_cmd)
    print(("ok    stops " if _hit else "FAIL  misses ") + _why)
    if not _hit:
        FAILS.append(_why)

for _cmd in MUST_PASS:
    _hit = _is_destructive(_cmd)
    print(("ok    allows " if not _hit else "FAIL  refuses ") + _cmd
          + (f"   [{_hit}]" if _hit else ""))
    if _hit:
        FAILS.append(_cmd)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print(f"all {len(MUST_STOP) + len(MUST_PASS)} destructive guard checks passed")
