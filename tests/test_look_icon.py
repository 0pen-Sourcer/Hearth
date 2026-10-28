"""Hearth's icon follows the saved look everywhere it shows.

Only the tray tinted the flame to the accent; the window, the tab icon and
desktop notices switched between Classic and the rest, so with a sage accent
the tray showed a green flame and the window an orange one.

    .\\.venv\\Scripts\\python.exe tests\\test_look_icon.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = tempfile.mkdtemp(prefix="hearth-lookicon-")
os.environ["HEARTH_WORKSPACE"] = WS
os.environ["JARVIS_WORKSPACE"] = WS
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"
with open(os.path.join(WS, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"onboarded": True, "ui_theme": "dark", "ui_accent": "sage"}, f)
sys.path.insert(0, ROOT)

from hearth import desktop_attach, reminders, tray  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def orange_share(img):
    """Share of the icon's strongly coloured pixels that are still orange."""
    import colorsys
    img = img.convert("RGBA").resize((64, 64))
    px = img.load()
    hot = orange = 0
    for y in range(64):
        for x in range(64):
            r, g, b, a = px[x, y]
            if a < 200:
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if s > 0.3 and 0.1 < l < 0.8:  # coloured, not the cream arch
                hot += 1
                orange += h < 0.13 or h > 0.97
    return orange / hot if hot else 1.0


from PIL import Image  # noqa: E402

sage_tray = tray._icon_for_look("dark", "sage")
check(f"the tray's flame takes the sage accent ({orange_share(sage_tray):.2f} orange)",
      orange_share(sage_tray) < 0.1)

# Windows wants an .ico for a window and its notices; other desktops a .png.
EXT = "ico" if sys.platform == "win32" else "png"
win = desktop_attach._icon_file("dark", "sage")
check(f"the window's icon for sage is an .{EXT} ({win})", bool(win) and win.lower().endswith("." + EXT))
check(f"and so does its flame ({orange_share(Image.open(win)):.2f} orange)",
      bool(win) and orange_share(Image.open(win)) < 0.1)

start = desktop_attach._icon_file(desktop_attach._saved_look("http://127.0.0.1:9"))
check("the window opens with the saved accent's icon", start == win)

note = reminders._find_icon()
check(f"notices use the saved look's icon ({note})", note == win)

ember = desktop_attach._icon_file("dark", "ember")
check(f"an ember flame reads as orange ({orange_share(Image.open(desktop_attach._icon_file('dark', 'ember'))):.2f})",
      orange_share(Image.open(desktop_attach._icon_file("dark", "ember"))) > 0.8)
check("ember is the shipped icon", bool(ember) and os.path.basename(ember) == f"icon.{EXT}")
classic = desktop_attach._icon_file("classic", "sage")
check("classic keeps the H whatever the accent", bool(classic) and os.path.basename(classic) == f"icon_classic.{EXT}")

import http.client  # noqa: E402
import threading  # noqa: E402

from hearth import web  # noqa: E402

srv = web._QuietHTTPServer(("127.0.0.1", 0), web.HearthHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
c.request("GET", "/api/look-icon?theme=dark&accent=ocean")
r = c.getresponse()
body = r.read()
c.close()
srv.shutdown()
check(f"the tab icon is served for a look ({r.status}, {r.getheader('Content-Type')})",
      r.status == 200 and r.getheader("Content-Type") == "image/png")
if r.status == 200:
    import io
    check("in that look's accent", orange_share(Image.open(io.BytesIO(body))) < 0.1)

# A new install starts in Rose on Dark; one set up before keeps the Classic it always had.
from hearth import look_icon as _li  # noqa: E402
_dt = getattr(_li, "default_theme", lambda s: "classic")
check("a new install opens in Dark, not Classic", _dt({}) == "dark" and _li.DEFAULT == ("dark", "rose"))
check("an install set up before the rename keeps Classic", _dt({"onboarded": True}) == "classic")
check("the app's own icon is in Rose, not the ember flame",
      os.path.isfile(os.path.join(ROOT, "assets", "icon_app.ico"))
      and orange_share(Image.open(os.path.join(ROOT, "assets", "icon_app.png"))) < 0.1)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all look icon checks passed")
