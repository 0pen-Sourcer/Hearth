"""A click lands on the control that is under the point.

smart_click maps a pixel from a capture to the screen and snaps it to a
real control. Snapping by nearest centre alone moved a precise click near the
end of a wide search box onto the small button beside it, because the
button's centre was closer than the box's. A point inside a control now means
that control.

    .\\.venv\\Scripts\\python.exe tests\\test_click_snap.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


from hearth import desktop_a11y as a                                 # noqa: E402

a._last = [
    {"name": "Search", "x": 200, "y": 15, "rect": (0, 0, 400, 30)},
    {"name": "Go", "x": 425, "y": 15, "rect": (410, 0, 440, 30)},
    {"name": "Toolbar", "x": 300, "y": 40, "rect": (0, 0, 600, 80)},
    {"name": "Bold", "x": 520, "y": 60, "rect": (505, 48, 535, 72)},
]
hit = a.element_near(380, 15)
check(f"a point near the end of a wide box clicks the box ({hit and hit['name']})", hit and hit["name"] == "Search")
hit = a.element_near(425, 12)
check(f"a point on the button clicks the button ({hit and hit['name']})", hit and hit["name"] == "Go")
hit = a.element_near(520, 60)
check(f"nested controls give the innermost ({hit and hit['name']})", hit and hit["name"] == "Bold")
a._last = [{"name": "Close", "x": 100, "y": 100}]
hit = a.element_near(130, 110)
check("a point outside every control still snaps to the nearest", hit and hit["name"] == "Close")
check("nothing near is nothing", a.element_near(900, 900) is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all click snapping checks passed")
