"""Hearth's icon follows the saved look everywhere it shows, as the Nook mark.

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


FG = (0xEC, 0xEB, 0xE7)


def _rgb(hexv):
    return tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))


def nook(img, accent):
    """Where the Nook mark's two parts sit: the dot in the accent and the
    bracket in the light foreground. Returns (dot pixels, bracket pixels,
    dot centre, bracket centre) on a 64 px copy."""
    from hearth.look_icon import ACCENTS
    want = _rgb(ACCENTS[accent])
    img = img.convert("RGBA").resize((64, 64))
    px = img.load()
    dot, br = [], []
    for y in range(64):
        for x in range(64):
            r, g, b, a = px[x, y]
            if a < 200:
                continue
            if sum(abs(c - w) for c, w in zip((r, g, b), want)) < 40:
                dot.append((x, y))
            elif sum(abs(c - w) for c, w in zip((r, g, b), FG)) < 40:
                br.append((x, y))

    def mid(pts):
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else (0, 0)
    return len(dot), len(br), mid(dot), mid(br)


def is_nook(img, accent):
    """The dot is a solid patch of the accent, the bracket is there, and the
    dot sits below and right of the bracket's corner (the old flame sat
    centred under its arch)."""
    nd, nb, (dx, dy), (bx, by) = nook(img, accent)
    return nd >= 80 and nb >= 80 and dx > bx + 6 and dy > by + 6


from PIL import Image  # noqa: E402

sage_tray = tray._icon_for_look("dark", "sage")
check(f"the tray shows the Nook with a sage dot {nook(sage_tray, 'sage')[:2]}", is_nook(sage_tray, "sage"))
fb = tray._icon_image()
check("the tray's own fallback is the Nook in Rose, not a drawn I", fb is not None and is_nook(fb, "rose"))

# Windows wants an .ico for a window and its notices; other desktops a .png.
EXT = "ico" if sys.platform == "win32" else "png"
win = desktop_attach._icon_file("dark", "sage")
check(f"the window's icon for sage is an .{EXT} ({win})", bool(win) and win.lower().endswith("." + EXT))
check("and it is the Nook with a sage dot", bool(win) and is_nook(Image.open(win), "sage"))

start = desktop_attach._icon_file(desktop_attach._saved_look("http://127.0.0.1:9"))
check("the window opens with the saved accent's icon", start == win)

note = reminders._find_icon()
check(f"notices use the saved look's icon ({note})", note == win)

ember = desktop_attach._icon_file("dark", "ember")
check("ember draws the Nook with an ember dot", bool(ember) and is_nook(Image.open(ember), "ember"))
for acc in ("ocean", "violet", "mono", "rose"):
    check(f"{acc} draws the Nook with its own dot", is_nook(Image.open(desktop_attach._icon_file("light", acc)), acc))
# A flame tinted by an older version sits in the same folder; it must not be reused.
_old = os.path.join(os.path.expanduser("~"), ".hearth", "icons", f"icon-ocean.{EXT}")
check("an icon saved by an older version is not reused",
      os.path.abspath(desktop_attach._icon_file("dark", "ocean")) != os.path.abspath(_old))
from hearth import look_icon as _lim  # noqa: E402
small = _lim.image("dark", "rose", 16)
check("the 16 px icon still shows a dot and a bracket",
      small is not None and nook(small.resize((64, 64), Image.NEAREST), "rose")[0] >= 40)
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
    check("in that look's accent", is_nook(Image.open(io.BytesIO(body)), "ocean"))

# A new install starts in Rose on Dark; one set up before keeps the Classic it always had.
from hearth import look_icon as _li  # noqa: E402
_dt = getattr(_li, "default_theme", lambda s: "classic")
check("a new install opens in Dark, not Classic", _dt({}) == "dark" and _li.DEFAULT == ("dark", "rose"))
check("an install set up before the rename keeps Classic", _dt({"onboarded": True}) == "classic")
check("the app's own icon is the Nook in Rose",
      os.path.isfile(os.path.join(ROOT, "assets", "icon_app.ico"))
      and is_nook(Image.open(os.path.join(ROOT, "assets", "icon_app.png")), "rose"))
_ico = Image.open(os.path.join(ROOT, "assets", "icon_app.ico"))
check("and its .ico holds the Nook too", is_nook(_ico, "rose"))
check("the shipped icon.png is the Nook in Rose", is_nook(Image.open(os.path.join(ROOT, "assets", "icon.png")), "rose"))

# The window draws the same mark for every look but Classic.
ui = open(os.path.join(ROOT, "hearth", "ui.html"), encoding="utf-8").read()
check("the window's top bar and empty chat draw the Nook",
      ui.count("M16 40V22a6 6 0 0 1 6-6h18") >= 3 and "hm-flame" not in ui and "hm-arch" not in ui)
check("the tab icon asks for the new art, not a cached flame", "look-icon?v=" in ui or "&v=" in ui.split("api/look-icon", 1)[-1][:200])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all look icon checks passed")
