"""The Team room's walk cycles: side-on, the legs pass each other between steps."""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEAM = os.path.join(ROOT, "assets", "team")
failed = 0


def check(label, cond):
    global failed
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failed += 1


man = json.load(open(os.path.join(TEAM, "manifest.json"), encoding="utf-8"))
cw, ch = man["cell"]
check("the atlas has a passing step after each side stride",
      "walk_side_3" in man["frames"] and "walk_side_4" in man["frames"])


def legs(atlas, name):
    i = man["frames"].index(name)
    a = np.asarray(atlas.crop((i * cw, 0, (i + 1) * cw, ch)))[..., 3] > 0
    ys = np.nonzero(a.any(axis=1))[0]
    lo = ys.max() - int((ys.max() - ys.min()) * 0.25)
    return a[lo:ys.max() + 1]


def spread(m):
    """How far apart the feet are: the width of the lowest rows."""
    xs = np.nonzero(m[-6:].any(axis=0))[0]
    return xs.max() - xs.min() if len(xs) else 0


if "walk_side_3" in man["frames"]:
    for role in man["roles"]:
        atlas = Image.open(os.path.join(TEAM, f"{role}.png")).convert("RGBA")
        stride, passing = spread(legs(atlas, "walk_side_1")), spread(legs(atlas, "walk_side_3"))
        check(f"{role}: the feet come together at the passing step ({stride} -> {passing} px)",
              passing < stride * 0.75)

sys.exit(1 if failed else 0)
