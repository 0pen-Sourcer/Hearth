"""Hearth's icon in the saved look.

Classic shows its own mark, the i with a flame. Every other theme shows the
Nook: a rounded corner bracket with a dot in the accent colour, on a dark tile.
The tray, the window, the page's tab icon and desktop notices all take it from
here, so they never disagree.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional, Tuple

ACCENTS = {"ember": "#d9774a", "sage": "#74a883", "ocean": "#5b93c2", "rose": "#cf7089",
           "violet": "#8b6cf0", "mono": "#a3a09a"}
DEFAULT = ("dark", "rose")


def default_theme(settings: dict) -> str:
    """The look when none was picked: Rose on Dark for a new install, Classic for
    one set up before the rename, which never saved a choice and has always had it."""
    return "classic" if (settings or {}).get("onboarded") else DEFAULT[0]
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
        ws = _get("WORKSPACE") or __import__("hearth").default_workspace()
        with open(os.path.join(ws, "settings.json"), encoding="utf-8") as f:
            s = json.load(f) or {}
        return (str(s.get("ui_theme") or default_theme(s)), str(s.get("ui_accent") or DEFAULT[1]))
    except Exception:
        return DEFAULT


def _key(theme: str, accent: str) -> str:
    if theme == "classic":
        return "classic"
    return accent if accent in ACCENTS else "ember"


# The Nook in its 64 unit box: the bracket's path (16,40) up to (16,22), a
# radius 6 corner, across to (40,16), stroke 6; the dot at (38,38), radius 9.
TILE = (0x1C, 0x19, 0x16)
FG = (0xEC, 0xEB, 0xE7)
VERSION = "nook1"


def _rgb(hexv: str) -> tuple:
    return tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))


def draw(accent: str, size: int = 256, tile: bool = True, shown: int = 0):
    """The Nook as a PIL image, size x size. On its dark rounded tile by
    default; without, on transparency in the foreground colour. `shown` is the
    size it will be scaled to on screen, when that is smaller (the tray)."""
    from PIL import Image, ImageDraw  # type: ignore
    ss = 4 if size >= 64 else 8
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if tile:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=round(S * 0.22), fill=TILE + (255,))
    # The mark spans 13..47 of its box; centre it, and on the tile give it
    # about 60% of the width. Tray and taskbar sizes get a larger mark and a
    # slightly heavier bracket so it stays two parts at 16 px.
    at = shown or size
    grow = 1.12 if at >= 48 else (1.22 if at >= 32 else 1.32)
    k = S / 64 * (grow if tile else 1.0)
    off = S / 2 - 30 * k

    def P(x, y):
        return (off + x * k, off + y * k)

    w = (6 if at >= 32 else 6.6) * k
    fg = FG + (255,)
    r = w / 2
    # Straight legs, then the corner as an arc, then round caps.
    d.line([P(16, 40), P(16, 22)], fill=fg, width=round(w))
    d.line([P(22, 16), P(40, 16)], fill=fg, width=round(w))
    cx, cy = P(22, 22)
    R = 6 * k
    d.arc([cx - R - r, cy - R - r, cx + R + r, cy + R + r], 180, 270, fill=fg, width=round(w))
    for x, y in ((16, 40), (40, 16), (16, 22), (22, 16)):
        px, py = P(x, y)
        d.ellipse([px - r, py - r, px + r, py + r], fill=fg)
    dx, dy = P(38, 38)
    dr = 9 * k
    d.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=_rgb(ACCENTS.get(accent, ACCENTS["rose"])) + (255,))
    return img.resize((size, size), Image.LANCZOS)


def save(accent: str, path: str, ext: str) -> None:
    """Write the tile as a .png (1024 px) or an .ico with each size drawn on
    its own rather than shrunk from the largest."""
    if ext == "ico":
        frames = [draw(accent, s) for s, _ in _ICO_SIZES]
        frames[-1].save(path, format="ICO", sizes=_ICO_SIZES, append_images=frames[:-1])
    else:
        draw(accent, 1024).save(path, format="PNG")


def image(theme: str, accent: str, size: int = 64, shown: int = 0):
    """The icon as a PIL image, or None without Pillow or the art."""
    if _key(theme, accent) != "classic":
        try:
            return draw(_key(theme, accent), size, shown=shown)
        except Exception:
            pass
    f = file(theme, accent, "png")
    if not f:
        return None
    try:
        from PIL import Image  # type: ignore
        return Image.open(f).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def file(theme: str, accent: str, ext: str = "ico") -> Optional[str]:
    """A file of the icon (.ico or .png). The shipped art for Classic; the
    Nook in the accent, drawn once, under ~/.hearth/icons for the others."""
    d = _assets()
    if not d:
        return None
    k = _key(theme, accent)
    if k == "classic":
        f = os.path.join(d, "icon_classic." + ext)
        return f if os.path.isfile(f) else None
    out_dir = os.path.join(os.path.expanduser("~"), ".hearth", "icons")
    out = os.path.join(out_dir, f"icon-{VERSION}-{k}.{ext}")
    try:
        if os.path.isfile(out):
            return out
        os.makedirs(out_dir, exist_ok=True)
        tmp = out + ".tmp"
        save(k, tmp, ext)
        os.replace(tmp, out)
        return out
    except Exception:
        f = os.path.join(d, "icon." + ext)
        return f if os.path.isfile(f) else None
