"""Uninstalling Inglen takes only what the installer and the app put in its folder.

The install folder can be one the person picked that already held their own
files. Builds a stub installer from Hearth.iss (StubTest: its own AppId and name,
no shortcuts from older versions removed, nothing launched), installs it silently
into a temp folder that holds an unrelated file and folder, adds what the app
writes there while it runs, uninstalls, and checks that their files are still
there and Inglen's are gone. The real install's uninstall entry is never touched.

Windows with Inno Setup 6.

    python scripts/test_uninstall_scope.py           # the script as it is
    python scripts/test_uninstall_scope.py --sweep   # with the old whole-folder sweep: must fail
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import winreg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISCC = next((p for p in (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
                         os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")) if os.path.isfile(p)), "")
STUB_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{5D0C8E21-7A43-4B6E-9F12-3C8A0B7E6D59}_is1"
REAL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7E2B6A4-1C9F-4F3D-9A6E-7E5C2F0A1D34}_is1"
OLD_SWEEP = ('Type: filesandordirs; Name: "{app}\\_internal"\n'
             'Type: filesandordirs; Name: "{app}"\n')

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def key_values(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            out, i = {}, 0
            while True:
                try:
                    n, v, _t = winreg.EnumValue(k, i)
                except OSError:
                    return out
                out[n] = v
                i += 1
    except OSError:
        return None


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def main() -> int:
    if not ISCC:
        print("Inno Setup 6 isn't installed here; skipped")
        return 0
    sweep = "--sweep" in sys.argv
    tmp = tempfile.mkdtemp(prefix="inglen-uninstall-")
    payload = os.path.join(tmp, "payload", "Hearth")
    for name in ("Hearth.exe", "Hearth-cli.exe", "_HearthWindow.exe"):
        write(os.path.join(payload, name), b"MZ stub, never run")
    write(os.path.join(payload, "Hearth-cli.bat"), b"@echo stub\r\n")
    with open(os.path.join(ROOT, "assets", "icon_app.ico"), "rb") as f:
        write(os.path.join(payload, "_internal", "assets", "icon_app.ico"), f.read())
    write(os.path.join(payload, "_internal", "hearth", "__init__.py"), b"")

    script = os.path.join(ROOT, "Hearth.iss")
    if sweep:
        with open(script, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"(\[UninstallDelete\]\n(?:;[^\n]*\n)*)(?:Type:[^\n]*\n)+", lambda m: m.group(1) + OLD_SWEEP,
                      text, count=1)
        script = os.path.join(ROOT, "_uninstall_scope_sweep.iss")      # beside Hearth.iss: it names LICENSE, assets\
        with open(script, "w", encoding="utf-8") as f:
            f.write(text)
    out_dir = os.path.join(tmp, "out")
    try:
        r = subprocess.run([ISCC, "/Q", "/DStubTest", f"/DSrcDir={payload}", f"/O{out_dir}", "/FStubSetup", script],
                           capture_output=True, text=True, cwd=ROOT)
    finally:
        if sweep:
            os.remove(script)
    check("the stub installer compiles", r.returncode == 0)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        return 1

    real_before = key_values(REAL_KEY)
    target = os.path.join(tmp, "Their folder")
    write(os.path.join(target, "thesis.docx"), b"four years of work")
    write(os.path.join(target, "photos", "trip.jpg"), b"jpeg")

    setup = os.path.join(out_dir, "StubSetup.exe")
    r = subprocess.run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", f"/DIR={target}",
                        "/TASKS=", "/LOG=" + os.path.join(tmp, "install.log")], timeout=300)
    check("the stub installs into a folder that already holds their files", r.returncode == 0
          and os.path.isfile(os.path.join(target, "Hearth.exe")))
    check("under its own uninstall entry", key_values(STUB_KEY) is not None)

    # What the app writes in its folder while it runs.
    made = [os.path.join(target, "_internal", "llamacpp", "b6000", "llama-server.exe"),
            os.path.join(target, "_internal", "_patch_backup", "hearth", "tools.py"),
            os.path.join(target, "_internal", "_patch_inflight.json"),
            os.path.join(target, "realtimesst.log"),
            os.path.join(target, "_code_before_sync_20261102_0046", "hearth", "web.py")]
    for p in made:
        write(p)

    uninst = os.path.join(target, "unins000.exe")
    subprocess.run([uninst, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
    deadline = time.time() + 120          # the uninstaller hands off to a copy of itself and returns at once
    while time.time() < deadline and (os.path.exists(uninst) or key_values(STUB_KEY) is not None):
        time.sleep(0.5)
    time.sleep(1.0)

    check("their file is still there", os.path.isfile(os.path.join(target, "thesis.docx")))
    check("and their folder with what's in it", os.path.isfile(os.path.join(target, "photos", "trip.jpg")))
    check("Inglen's own files are gone",
          not any(os.path.exists(os.path.join(target, n)) for n in ("Hearth.exe", "Hearth-cli.bat", "_internal",
                                                                     "unins000.exe", "unins000.dat")))
    check("and what it wrote while running", not any(os.path.exists(p) for p in made))
    check("the stub's uninstall entry is gone", key_values(STUB_KEY) is None)
    check("the real install's entry is as it was", key_values(REAL_KEY) == real_before)

    empty = os.path.join(tmp, "Own folder")
    r = subprocess.run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", f"/DIR={empty}",
                        "/TASKS="], timeout=300)
    write(os.path.join(empty, "realtimesst.log"))
    subprocess.run([os.path.join(empty, "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                   timeout=300)
    deadline = time.time() + 120
    while time.time() < deadline and (os.path.exists(empty) or key_values(STUB_KEY) is not None):
        time.sleep(0.5)
    check("a folder of its own is removed whole", not os.path.exists(empty))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"{len(FAILS)} failed" if FAILS else "all passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
