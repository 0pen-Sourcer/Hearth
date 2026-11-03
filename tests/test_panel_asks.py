"""The desktop panel shows the approval question on an agent brain too.

Claude Code reaches some desktop tools (computer_click, focus_window and the
other deferred ones) through use_tool. Its permission hook asked about those
as "use_tool", which isn't a desktop step, so the question went only to the
window, worded as a lookup, and never to the panel. Now the hook asks about
the tool use_tool runs: the panel comes up and carries the question, and
whichever of the panel or the window answers first wins. Stand-ins for the
panel's drawing and the glow; the real check wrapper, hook and panel state.

    .\\.venv\\Scripts\\python.exe tests\\test_panel_asks.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _home as H  # noqa: E402  temporary home and workspace

os.environ.update(HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
for k in ("HEARTH_AUTO_APPROVE", "JARVIS_AUTO_APPROVE"):
    os.environ.pop(k, None)

from hearth import auto_review as A, claude_brain as cb, cu_panel, desktop_session as ds, tools  # noqa: E402

tools.WORKSPACE = H.WORKSPACE
json.dump({"approval_mode": "ask"}, open(os.path.join(H.WORKSPACE, "settings.json"), "w", encoding="utf-8"))
FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


# Nothing is drawn: no panel window, no glow, no foreground juggling.
cu_panel._ensure = lambda: None
cu_panel._WIN = True
ds._WIN = True
ds._enabled = lambda key="cu_glow": True
ds._Glow.show = staticmethod(lambda: None)
ds._Glow.hide = staticmethod(lambda: None)
ds._foreground = lambda: None
from hearth import activity_hud  # noqa: E402

activity_hud.activity = activity_hud.for_tool = activity_hud.done = lambda *a, **k: None

window_asked: list = []
release = threading.Event()


def window_check(name, args):
    """The window's question: waits for a click that, here, never comes."""
    window_asked.append(name)
    release.wait(6)
    return "timeout"


kw, tok = ds.watch("click Start for me", {"permission_check": window_check, "emit": lambda k, **f: None})
A.begin_turn("c1", "click Start for me", [], None)
cb._checks[cb._TOKEN] = kw["permission_check"]
cb._turns[cb._TOKEN] = A.current()

out: dict = {}
t = threading.Thread(target=lambda: out.update(cb.decide(
    cb._TOKEN, "mcp__hearth__use_tool", {"name": "computer_click", "arguments": {"x": 10, "y": 1060}})))
t.start()
asked = None
deadline = time.time() + 4
while time.time() < deadline and asked is None:
    asked = cu_panel.state()["ask"]
    time.sleep(0.05)
if asked:
    cu_panel.press("allow")
t.join(8)
release.set()

check(f"a desktop step through use_tool brings the panel up ({cu_panel.state()['phase']})",
      cu_panel.state()["phase"] == "work")
check(f"and the panel carries its question ({asked})", bool(asked) and asked.get("tool") == "computer_click")
check(f"the window is asked too, as the same tool ({window_asked})", window_asked[:1] == ["computer_click"])
check(f"Allow on the panel answers it ({out.get('behavior')})", out.get("behavior") == "allow")

A.end_turn()
ds._active = False
ds._owner = None

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all panel-ask checks passed")
