"""Opening apps, files and links, Open with, and away-time work on Linux and
macOS, not only Windows.

Linux apps come from the desktop's own .desktop entries (Flatpak and Snap ones
included), so "open Spotify" finds an app whose command isn't `spotify`. Files
and links go to `open` / `xdg-open` (os.startfile is Windows-only). Away time
comes from IOKit on a Mac and the desktop's idle monitor on Linux.

    .\\.venv\\Scripts\\python.exe tests\\test_platform_standins.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import desktop_entries as de, openers, reminders, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# A desktop's app folders: the user's own and the system's.
root = tempfile.mkdtemp(prefix="hearth-xdg-")
home_apps = os.path.join(root, "home", "applications")
sys_apps = os.path.join(root, "usr", "applications")
os.makedirs(home_apps)
os.makedirs(sys_apps)


def entry(folder, fid, body):
    with open(os.path.join(folder, fid), "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\n" + body)


entry(sys_apps, "com.spotify.Client.desktop", "Name=Spotify\nExec=/usr/bin/flatpak run com.spotify.Client %U\nIcon=spotify\n")
entry(sys_apps, "org.gnome.TextEditor.desktop", "Name=Text Editor\nExec=gnome-text-editor %U\nMimeType=text/plain;\n")
entry(sys_apps, "mousepad.desktop", "Name=Mousepad\nExec=mousepad %F\nMimeType=text/plain;text/x-log;\n")
entry(sys_apps, "helper.desktop", "Name=Spotify Helper\nExec=helper\nNoDisplay=true\n")
entry(sys_apps, "gone.desktop", "Name=Gone\nExec=gone\nHidden=true\n")
entry(home_apps, "mousepad.desktop", "Name=Mousepad (mine)\nExec=mousepad --mine %F\nMimeType=text/plain;\n")
os.environ["XDG_DATA_HOME"] = os.path.join(root, "home")
os.environ["XDG_DATA_DIRS"] = os.path.join(root, "usr")

names = sorted(str(e["name"]) for e in de.entries())
check("hidden entries are left out, the user's own copy wins", names ==
      ["Mousepad (mine)", "Spotify", "Spotify Helper", "Text Editor"])
check("an app is found by its name though its command isn't that name",
      de.find_app("spotify")["id"] == "com.spotify.Client.desktop")
check("one shown nowhere isn't picked for a name", de.find_app("spotify helper") is None)
check("a partial name finds it", de.find_app("text")["name"] == "Text Editor")
check("its command gets the file in place of %U",
      de.argv(de.find_app("spotify"), ["/tmp/a b.mp3"]) == ["/usr/bin/flatpak", "run", "com.spotify.Client", "/tmp/a b.mp3"])

de.mime_of = lambda p: "text/plain"
de.default_for = lambda m: "org.gnome.TextEditor.desktop"
real_platform = sys.platform
sys.platform = "linux"
try:
    listed = openers.openers("/home/me/notes.txt")
finally:
    sys.platform = real_platform
check("Open with on Linux lists the desktop's apps, the default first",
      [o["name"] for o in listed] == ["Text Editor", "Mousepad (mine)"] and listed[0]["default"])

launched = []
de.launch = lambda e, files=None: launched.append((e["id"], files)) or 1
sys.platform = "linux"
try:
    openers.open_with("/home/me/notes.txt", listed[1]["exe"])
    try:
        openers.open_with("/home/me/notes.txt", "/usr/bin/evil")
        refused = False
    except ValueError:
        refused = True
finally:
    sys.platform = real_platform
check("Open with starts the chosen app on the file", launched == [("mousepad.desktop", ["/home/me/notes.txt"])])
check("an app not on the list is refused", refused)

# Opening an app by name on Linux, with no such command on PATH.
launched.clear()
real_which = shutil.which
shutil.which = lambda c, *a, **k: None
tools._bring_newest_to_front = lambda before: None
sys.platform = "linux"
try:
    out = tools._open_app({"name": "Spotify"})
finally:
    sys.platform = real_platform
    shutil.which = real_which
check("'open Spotify' starts the desktop's Spotify", launched and launched[0][0] == "com.spotify.Client.desktop"
      and "Spotify" in out)

# Files and links with the default app.
started, startfile = [], []
tools.popen_outside = lambda args, **k: started.append(list(args))
real_startfile = getattr(os, "startfile", None)
os.startfile = lambda *a, **k: startfile.append(a)
for plat, tool in (("darwin", "open"), ("linux", "xdg-open")):
    started.clear()
    sys.platform = plat
    try:
        tools.open_outside("/home/me/report.pdf")
        tools._open_in_browser({"url": "example.com"})
    finally:
        sys.platform = real_platform
    check(f"{plat}: a file and a link open with {tool}",
          [a[0].rsplit("/", 1)[-1] for a in started] == [tool, tool] and started[1][1] == "https://example.com"
          and not startfile)
if real_startfile:
    os.startfile = real_startfile

# Away time.
real_run = subprocess.run


class _R:
    def __init__(self, out):
        self.returncode, self.stdout = 0, out


for plat, which, out, want in (
        ("darwin", "ioreg", '    | |   "HIDIdleTime" = 125000000000\n', 125.0),
        ("linux", "gdbus", "(uint64 42000,)\n", 42.0)):
    shutil.which = lambda c, *a, w=which, **k: "/usr/bin/" + c if c == w else None
    subprocess.run = lambda cmd, *a, o=out, **k: _R(o)
    sys.platform = plat
    try:
        got = reminders.idle_seconds()
    finally:
        sys.platform = real_platform
        shutil.which, subprocess.run = real_which, real_run
    check(f"{plat}: time away from the keyboard is read ({got})", got == want)

shutil.rmtree(root, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all platform stand-in checks passed")
