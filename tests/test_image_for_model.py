"""A picture goes to the model no larger than 1280 on its long side.

A full-screen screenshot went in at 2560x1440: about 3,400 tokens, and a 9B on
an 8 GB card took minutes to read each one. Now it's shrunk for the model (the
file on disk is left alone), with the scale in the note so positions read off
it still map back to the screen.

    .\\.venv\\Scripts\\python.exe tests\\test_image_for_model.py
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="hearth-img-")
os.environ.update(JARVIS_WORKSPACE=work, HEARTH_WORKSPACE=work, HEARTH_NO_ACTIVITY_LOG="1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from hearth import headless  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def size_of(url):
    data = base64.b64decode(url.split(",", 1)[1])
    return Image.open(io.BytesIO(data)).size


big = os.path.join(work, "shot.png")
Image.new("RGB", (2560, 1440), (30, 30, 30)).save(big)
url, note = headless.image_for_model(big)
check(f"a 2560x1440 screenshot goes to the model at 1280x720 ({size_of(url)})", size_of(url) == (1280, 720))
check(f"with the scale, so positions map back to the screen ({note.strip()!r})", "2560x1440" in note and "2.00" in note)
check("the file on disk is left as it was", Image.open(big).size == (2560, 1440))

small = os.path.join(work, "small.png")
Image.new("RGB", (800, 600), (30, 30, 30)).save(small)
url, note = headless.image_for_model(small)
check("a picture already small enough goes as it is, with no note", size_of(url) == (800, 600) and note == "")

src = open(os.path.join(ROOT, "hearth", "headless.py"), encoding="utf-8").read()
check("the engine sends tool pictures through it", "image_for_model(_ip)" in src)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all image size checks passed")
