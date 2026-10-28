"""Shortcuts made before the rename are renamed to Inglen with the app icon, and
only this app's own: real .lnk files in temp folders, through WScript.Shell.

    .\\.venv\\Scripts\\python.exe tests\\test_shortcut_rebrand.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


if sys.platform != "win32":
    print("skip: Windows shortcuts")
    sys.exit(0)

import win32com.client  # noqa: E402

from hearth import install_shortcuts as S  # noqa: E402

base = tempfile.mkdtemp(prefix="hearth-lnk-")
app = os.path.join(base, "AppData", "Hearth")
os.makedirs(app)
for f in ("Hearth.exe", "Hearth-cli.bat", "Other.exe"):
    open(os.path.join(app, f), "w").close()
desk, progs = os.path.join(base, "Desktop"), os.path.join(base, "Programs")
os.makedirs(os.path.join(progs, "Hearth"))
os.makedirs(desk)
shell = win32com.client.Dispatch("WScript.Shell")


def lnk(path, target, args=""):
    sc = shell.CreateShortcut(path)
    sc.TargetPath = target
    sc.Arguments = args
    sc.WorkingDirectory = app
    sc.Save()


lnk(os.path.join(desk, "Hearth.lnk"), os.path.join(app, "Hearth.exe"), "--open")
lnk(os.path.join(desk, "Hearth CLI.lnk"), os.path.join(app, "Hearth-cli.bat"))
lnk(os.path.join(progs, "Hearth", "Hearth.lnk"), os.path.join(app, "Hearth.exe"))
# Someone else's shortcut that happens to be called Hearth.
os.makedirs(os.path.join(base, "Desktop2"))
lnk(os.path.join(base, "Desktop2", "Hearth.lnk"), os.path.join(app, "Other.exe"))

n = S.rebrand if hasattr(S, "rebrand") else None
done = n(desk, progs) if n else 0
check(f"three of this app's shortcuts renamed ({done})", done == 3)
check("the desktop now says Inglen and Inglen CLI",
      os.path.isfile(os.path.join(desk, "Inglen.lnk")) and os.path.isfile(os.path.join(desk, "Inglen CLI.lnk"))
      and not os.path.isfile(os.path.join(desk, "Hearth.lnk")))
check("the Start menu folder is Inglen, the old one gone",
      os.path.isfile(os.path.join(progs, "Inglen", "Inglen.lnk")) and not os.path.isdir(os.path.join(progs, "Hearth")))
if os.path.isfile(os.path.join(desk, "Inglen.lnk")):
    sc = shell.CreateShortcut(os.path.join(desk, "Inglen.lnk"))
    check(f"it still opens the same program with the same arguments ({sc.TargetPath} {sc.Arguments})",
          os.path.samefile(sc.TargetPath, os.path.join(app, "Hearth.exe")) and sc.Arguments == "--open")
    check(f"with the app icon ({sc.IconLocation})", "icon_app.ico" in sc.IconLocation)
n2 = S.rebrand(os.path.join(base, "Desktop2"), os.path.join(base, "none")) if n else 0
check("a shortcut to another program is left alone",
      n2 == 0 and os.path.isfile(os.path.join(base, "Desktop2", "Hearth.lnk")))
check("running it again changes nothing", (S.rebrand(desk, progs) if n else -1) == 0)

if FAILS:
    print(f"\n{len(FAILS)} check(s) failed")
    sys.exit(1)
print("\nall shortcut rebrand checks passed")
