"""While JARVIS drives the desktop, it shows; when the turn ends, the screen comes back.

The first desktop action of a turn starts a session (edge glow, pill, the window
that was in front remembered); the end of the turn, however it ends, closes it,
puts that window back in front and stops taskbar flashing. With an agent brain the
tools run in a server that dies with the turn, so it reports to Hearth instead of
drawing its own.

    .\\.venv\\Scripts\\python.exe tests\\test_desktop_session.py
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hearth import activity_hud, claude_brain, desktop_session as ds, headless, tools  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


check("the edge breathes between dim and full",
      abs(ds.breath(0) - ds.LOW) < 1e-6 and abs(ds.breath(ds.BREATH_S / 2) - ds.HIGH) < 1e-6)

seen = {"glow": [], "front": [], "flash": [], "chime": 0, "done": 0, "hud": []}
ds._WIN = True
ds._enabled = lambda: True
ds._foreground = lambda: 4242
ds._Glow.show = classmethod(lambda cls: seen["glow"].append("show"))
ds._Glow.hide = classmethod(lambda cls: seen["glow"].append("hide"))
ds.bring_to_front = lambda h: seen["front"].append(h) or True
ds._stop_flash = lambda h: seen["flash"].append(h)
ds._chime = lambda: seen.__setitem__("chime", seen["chime"] + 1)
activity_hud.done = lambda *a, **k: seen.__setitem__("done", seen["done"] + 1)
activity_hud.for_tool = lambda n, a: seen["hud"].append(n)

ds.begin("read_file", {})
check("reading a file doesn't start a session", not ds.is_active() and not seen["glow"])
ds.begin("desktop_click", {"name": "Save"})
ds.begin("desktop_type", {"text": "hi"})
check("the first desktop action starts one glow", ds.is_active() and seen["glow"] == ["show"])
check("the pill says what it's doing", seen["hud"] == ["desktop_click", "desktop_type"])
ds.raised(777)
check("the end reports a session ran", ds.end() is True)
check("the glow goes", seen["glow"][-1] == "hide")
check("the window that was in front comes back", seen["front"] == [4242])
check("a raised window stops flashing", 777 in seen["flash"])
check("a chime and Done mark the end", seen["chime"] == 1 and seen["done"] == 1)
check("a second end does nothing", ds.end() is False and seen["chime"] == 1)

# Inside an agent brain's tool server: report to Hearth, draw nothing there.
sent = []
tools._desktop_to_hearth = lambda n, a: sent.append(n)
os.environ["HEARTH_BRAIN_URL"] = "http://127.0.0.1:9"
tools.execute_tool("desktop_type", {})          # missing text: refused after the report
check("the tool server forwards the action", sent == ["desktop_type"] and not ds.is_active())
del os.environ["HEARTH_BRAIN_URL"]
tools.execute_tool("desktop_type", {})
check("in Hearth itself the action starts the session", ds.is_active())
ds.end()

check("a stranger can't start a session", claude_brain.desktop("nope", "desktop_click", {}) == {"ok": False}
      and not ds.is_active())
claude_brain.desktop(claude_brain._TOKEN, "desktop_click", {})
check("the brain's server can", ds.is_active())


# A click on a control in a window behind another one brings its window forward
# first: the click is a real mouse click and would land on whatever is on top.
from hearth import desktop_a11y  # noqa: E402

order = []


class _Top:
    NativeWindowHandle = 555


class _Ctrl:
    def GetTopLevelControl(self):
        return _Top()

    def Click(self, **k):
        order.append("click")


ds.bring_to_front = lambda h: order.append(("front", h)) or True
desktop_a11y._last = [{"backend": "uia", "ctrl": _Ctrl(), "name": "Close", "x": 1, "y": 1}]
desktop_a11y.click(name="Close")
check("a window behind comes forward before the click", order == [("front", 555), "click"])
order.clear()
ds._foreground = lambda: 555
desktop_a11y.click(name="Close")
check("a window already in front is left alone", order == ["click"])
ds._foreground = lambda: 4242
ds.bring_to_front = lambda h: seen["front"].append(h) or True


async def boom(*a, **k):
    raise RuntimeError("model went away")

headless._run_once = boom
try:
    asyncio.run(headless.run_once("hi"))
except RuntimeError:
    pass
check("a turn that fails still gives the screen back", not ds.is_active() and seen["front"][-1] == 4242)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all desktop session checks passed")
