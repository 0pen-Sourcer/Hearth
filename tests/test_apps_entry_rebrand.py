"""An install patched up from v0.7.5 still has the uninstall entry its old
installer wrote (Hearth, publisher 0pen-sourcer, no icon). The packaged app
rewrites it to Inglen with the app icon, only for the entry that points at its
own folder, and the installer stops reusing the old Start menu folder.

    .\\.venv\\Scripts\\python.exe tests\\test_apps_entry_rebrand.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _home  # noqa: E402,F401

from hearth import updater  # noqa: E402

FAILS: list = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


class FakeWinreg(types.ModuleType):
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 1, 2
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self, values):
        super().__init__("winreg")
        self.values = values            # {root: {name: value}}

    def OpenKey(self, root, key, res=0, access=0):
        if root not in self.values:
            raise FileNotFoundError(key)
        return root

    def QueryValueEx(self, k, name):
        if name not in self.values[k]:
            raise FileNotFoundError(name)
        return self.values[k][name], 1

    def SetValueEx(self, k, name, res, typ, value):
        self.values[k][name] = value

    def CloseKey(self, k):
        pass


def run(values, app_dir):
    fake = FakeWinreg(values)
    real = sys.modules.get("winreg")
    sys.modules["winreg"] = fake
    try:
        updater._sync_registry_version(app_dir)
    finally:
        if real is not None:
            sys.modules["winreg"] = real
        else:
            sys.modules.pop("winreg", None)
    return fake.values


if os.name != "nt":
    print("SKIP Windows only")
    raise SystemExit(0)

app = tempfile.mkdtemp(prefix="inglen-app-")
os.makedirs(os.path.join(app, "_internal", "assets"))
icon = os.path.join(app, "_internal", "assets", "icon_app.ico")
open(icon, "wb").close()
old = {"DisplayName": "Hearth version 0.7.5-preview", "DisplayVersion": "0.7.5-preview",
       "Publisher": "0pen-sourcer", "InstallLocation": app + "\\"}

got = run({1: dict(old)}, app)[1]
check("name says Inglen and this version", got["DisplayName"] == f"Inglen version {updater.HEARTH_VERSION}", got)
check("version synced", got["DisplayVersion"] == updater.HEARTH_VERSION, got)
check("publisher is Inglen", got["Publisher"] == "Inglen", got)
check("icon is the app icon in assets", got.get("DisplayIcon") == icon, got)

other = dict(old, InstallLocation="D:\\Somewhere\\Else\\")
got = run({1: dict(other)}, app)[1]
check("another install's entry is left alone", got == other, got)

with open(os.path.join(ROOT, "Hearth.iss"), encoding="utf-8") as f:
    iss = f.read()
check("installer does not reuse the old Start menu folder", "\nUsePreviousGroup=no" in iss)
check("AppId unchanged", "AppId={{B7E2B6A4-1C9F-4F3D-9A6E-7E5C2F0A1D34}" in iss)
check("install folder unchanged", '#define MyAppName "Hearth"' in iss
      and "DefaultDirName={localappdata}\\{#MyAppName}" in iss)

print("\nOK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
raise SystemExit(1 if FAILS else 0)
