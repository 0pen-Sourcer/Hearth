"""Hearth's icon in the saved look.

Classic keeps the H. Every other theme shows the hearth with its flame in the
accent colour. The tray, the window, the page's tab icon and desktop notices
all take it from here, so they never disagree.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional, Tuple

ACCENTS = {"ember": "#d9774a", "sage": "#74a883", "ocean": "#5b93c2", "rose": "#cf7089",
           "violet": "#8b6cf0", "mono": "#a3a09a"}
DEFAULT = ("classic", "rose")
_ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _assets() -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.join(os.path.dirname(here), "assets"),
              os.path.join(getattr(sys, "_MEIPASS", ""), "assets")):
        if os.path.isfile(os.path.join(d, "icon.png")):
            return d
    return None


def saved() -> Tuple[str, str]:
    """The theme and accent the window last saved, read from the settings file
    so it works before the backend answers."""
    try:
        from .env import get as _get
        ws = _get("WORKSPACE") or os.path.join(os.path.expanduser("~"), "Jarvis")
        with open(os.path.join(ws, "settings.json"), encoding="utf-8") as f:
            s = json.load(f) or {}
        return (str(s.get("ui_theme") or DEFAULT[0]), str(s.get("ui_accent") or DEFAULT[1]))
    except Exception:
        return DEFAULT


def _key(theme: str, accent: str) -> str:
    if theme == "classic":
        return "classic"
    return accent if accent in ACCENTS else "ember"


def _tint(img, accent: str):
    """Recolour the flame. It is the only strongly orange part of the icon,
    so shifting the hue of those pixels is enough."""
    import colorsys
    hexv = ACCENTS[accent]
    th, _l, ts = colorsys.rgb_to_hls(*(int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a < 10:
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            # The flame and its edge where it blends into the dark; the cream
            # arch is the same hue but far lighter, so it is left alone.
            if s > 0.3 and (h < 0.13 or h > 0.97) and 0.1 < l < 0.8:
                nr, ng, nb = colorsys.hls_to_rgb(th, l, 0.05 if accent == "mono" else min(s, ts + 0.15))
                px[x, y] = (int(nr * 255), int(ng * 255), int(nb * 255), a)
    return img


def image(theme: str, accent: str, size: int = 64):
    """The icon as a PIL image, or None without Pillow or the art."""
    f = file(theme, accent, "png")
    if not f:
        return None
    try:
        from PIL import Image  # type: ignore
        return Image.open(f).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def file(theme: str, accent: str, ext: str = "ico") -> Optional[str]:
    """A file of the icon (.ico or .png). The shipped art for Classic and
    Ember; a tinted copy, made once, under ~/.hearth/icons for the others."""
    d = _assets()
    if not d:
        return None
    k = _key(theme, accent)
    if k in ("classic", "ember"):
        f = os.path.join(d, ("icon_classic." if k == "classic" else "icon.") + ext)
        return f if os.path.isfile(f) else None
    out_dir = os.path.join(os.path.expanduser("~"), ".hearth", "icons")
    out = os.path.join(out_dir, f"icon-{k}.{ext}")
    src = os.path.join(d, "icon.png")
    try:
        if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            return out
        from PIL import Image  # type: ignore
        img = _tint(Image.open(src).convert("RGBA").resize((256, 256), Image.LANCZOS), k)
        os.makedirs(out_dir, exist_ok=True)
        tmp = out + ".tmp"
        if ext == "ico":
            img.save(tmp, format="ICO", sizes=_ICO_SIZES)
        else:
            img.save(tmp, format="PNG")
        os.replace(tmp, out)
        return out
    except Exception:
        f = os.path.join(d, "icon." + ext)
        return f if os.path.isfile(f) else None
