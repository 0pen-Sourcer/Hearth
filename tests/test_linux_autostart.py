"""Start at login on Linux: an XDG autostart entry, and the switch says what is so.

It only knew the Windows Startup folder, so on Linux the switch did nothing.
Now ~/.config/autostart/inglen.desktop starts the tray at login: the AppImage
file itself (not its temporary mount), the installed program from the .deb, or
a source checkout. The entry is checked with desktop-file-validate and started
with dex, which runs autostart entries as a desktop session does, where they are
installed. Temp HOME and XDG_CONFIG_HOME.

    python tests/test_linux_autostart.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-autostart-home-")
WS = os.path.join(HOME, "Inglen")
os.makedirs(WS)
CFG = os.path.join(HOME, ".config")
os.environ.update(HOME=HOME, USERPROFILE=HOME, XDG_CONFIG_HOME=CFG, HEARTH_WORKSPACE=WS, JARVIS_WORKSPACE=WS,
                  HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
os.environ.pop("APPIMAGE", None)
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True}, f)
sys.path.insert(0, ROOT)

if not sys.platform.startswith("linux"):
    print("Linux only")
    sys.exit(0)

from hearth import install_shortcuts as sh  # noqa: E402

FAILS: list = []
ENTRY = os.path.join(CFG, "autostart", "inglen.desktop")


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def exec_args():
    return sh._exec_args(sh._linux_entry().get("Exec", ""))


def validate():
    if not shutil.which("desktop-file-validate"):
        return True
    r = subprocess.run(["desktop-file-validate", ENTRY], capture_output=True, text=True)
    if r.returncode or r.stdout.strip():
        print("      " + (r.stdout + r.stderr).strip())
    return r.returncode == 0 and "error" not in r.stdout.lower()


def dex_run():
    """Start the autostart entries as a session would; None where dex isn't installed."""
    if not shutil.which("dex"):
        return None
    return subprocess.run(["dex", "-a", "-s", os.path.join(CFG, "autostart")],
                          capture_output=True, text=True, timeout=30)


check("off to begin with", sh.is_autostart_enabled() is False)

# The AppImage, somewhere with a space in its path.
apps = os.path.join(HOME, "My Apps")
os.makedirs(apps)
appimage = os.path.join(apps, "Inglen-x86_64.AppImage")
said = os.path.join(HOME, "appimage_args.json")
with open(appimage, "w") as f:
    f.write(f"#!{sys.executable}\nimport json, sys\njson.dump(sys.argv, open({said!r}, 'w'))\n")
os.chmod(appimage, stat.S_IRWXU)
os.environ["APPIMAGE"] = appimage
check("switching it on writes the entry and says it is on", sh.set_autostart(True) is True and os.path.isfile(ENTRY))
check(f"the entry starts the AppImage file in the tray {exec_args()}", exec_args() == [appimage, "--background"])
check("the entry is a valid desktop entry", validate())
r = dex_run()
if r is not None:
    end = time.time() + 10
    while time.time() < end and not os.path.exists(said):
        time.sleep(0.1)
    got = json.load(open(said)) if os.path.exists(said) else []
    check(f"a session starting it runs the AppImage with --background {got}",
          got[:1] == [appimage] and got[1:] == ["--background"])
os.rename(appimage, appimage + ".moved")
check("with the AppImage moved away, the switch says off", sh.is_autostart_enabled() is False)
os.rename(appimage + ".moved", appimage)
check("put back, on again", sh.is_autostart_enabled() is True)
os.environ.pop("APPIMAGE")

# The .deb's program, /opt/inglen/inglen there; a stand-in here.
prog = os.path.join(HOME, "opt", "inglen", "inglen")
os.makedirs(os.path.dirname(prog))
shutil.copy(appimage, prog)
sys.frozen, real_exe = True, sys.executable
sys.executable = prog
try:
    sh.set_autostart(True)
    check(f"installed from the .deb, it starts the installed program {exec_args()}",
          exec_args() == [prog, "--background"])
finally:
    del sys.frozen
    sys.executable = real_exe

# From source: this checkout's tray.
sh.set_autostart(True)
args = exec_args()
check(f"from source, it starts this checkout's tray {args}",
      args == [sys.executable, "-m", "hearth.tray", "--background"]
      and sh._linux_entry().get("Path") == ROOT and validate())
try:
    proc = subprocess.Popen(args, cwd=sh._linux_entry().get("Path"), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            env=dict(os.environ, DISPLAY="", WAYLAND_DISPLAY=""))
    try:
        out, err = proc.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    check("the tray takes --background", "unrecognized arguments" not in err)
except OSError as e:
    check(f"the tray starts ({e})", False)

check("switching it off removes the entry and says off", sh.set_autostart(False) is False and not os.path.exists(ENTRY))

# Through the window's own API.
from hearth import web  # noqa: E402

srv = web.serve("127.0.0.1", 0)
port = srv.server_address[1]


def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/autostart", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


check("the window's switch reads off", post({}) == {"ok": True, "enabled": False})
check("turns it on", post({"enabled": True}) == {"ok": True, "enabled": True} and os.path.isfile(ENTRY))
check("and reads on", post({}) == {"ok": True, "enabled": True})
check("and off again", post({"enabled": False}) == {"ok": True, "enabled": False} and not os.path.exists(ENTRY))
srv.shutdown()

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all linux autostart checks passed")
