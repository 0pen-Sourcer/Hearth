"""The desktop pointer: the brand mark, its tip just off the click point.

Checks the drawing and the press timing without putting anything on screen."""

from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

work = tempfile.mkdtemp(prefix="hearth-agent-cursor-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hearth import agent_cursor as ac      # noqa: E402

FAILS = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def alpha_at(frame, x, y):
    n, x0, y0, w, h, data = frame
    if not (x0 <= x < x0 + w and y0 <= y < y0 + h):
        return 0
    return data[((y - y0) * w + (x - x0)) * 4 + 3]


rose = (0xCF, 0x70, 0x89)
covered = []
for scale in (1.0, 1.25, 1.5, 2.0):
    for heading in range(-180, 180, 15):
        for fill in (0.0, 1.0):
            f = ac.render(heading, fill, scale, rose)
            c = f[0] // 2
            if alpha_at(f, c, c):
                covered.append((scale, heading, fill))
check(f"the click point's pixel is never drawn over {covered[:3]}", not covered)


def centroid(frame):
    n, x0, y0, w, h, data = frame
    sx = sy = sa = 0
    for i in range(w * h):
        a = data[i * 4 + 3]
        if a:
            sx += (x0 + i % w) * a
            sy += (y0 + i // w) * a
            sa += a
    c = n // 2
    return sx / sa - c, sy / sa - c


for heading in (-135, 0, 90, 180, 45):
    cx, cy = centroid(ac.render(heading, 0.0, 1.25, rose))
    body = math.degrees(math.atan2(cy, cx))
    off = abs((body - (heading + 180) + 180) % 360 - 180)
    check(f"facing {heading} deg, the body trails behind the tip (off by {off:.0f} deg)", off < 15)

f0, f1 = ac.render(-135, 0.0, 1.0, rose), ac.render(-135, 1.0, 1.0, rose)
dc = ac._DOT_C
k = ac._UNIT
hx = ac._TIP - ac._GAP / k / math.sqrt(2)
dot = (f0[0] // 2 + int((dc - hx) * k), f0[0] // 2 + int((dc - hx) * k))
check("the dot is empty at rest and filled with the accent when pressed",
      alpha_at(f0, *dot) == 0 and alpha_at(f1, *dot) == 255)
check("a scaled display gets a larger canvas, drawn at its own pixels",
      ac.render(-135, 0, 1.5, rose)[0] > ac.render(-135, 0, 1.0, rose)[0])

sp = ac._Spring(0.0, 30.0)
sp.target = 1.0
peak = 0.0
for _ in range(200):
    sp.step(1 / 120)
    peak = max(peak, sp.x)
check(f"springs arrive without overshoot (peak {peak:.4f})", peak <= 1.0 + 1e-9 and sp.settled(0.01))

p = ac._Pointer()           # not started: nothing is drawn
p.press(True); p.press(False); p.press(True); p.press(False)
(a0, a1), (b0, b1) = p.pulses
check("a double click shows two separate pulses", b0 > a1 and a1 - a0 >= 0.09 and b1 - b0 >= 0.09)
p = ac._Pointer()
p.press(True)
check("a held button keeps the dot filled until it is released", p.pulses[0][1] is None)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all agent cursor checks passed")
