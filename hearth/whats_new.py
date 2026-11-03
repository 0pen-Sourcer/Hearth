"""When the window shows What's new.

Once per release, after an update, never on a fresh install. Settings keep the
last release a person saw it for (whats_new_seen). An install set up before
the screen existed has no such key but has been through setup, so it is an
update from Hearth; one with neither is new, and is marked seen on the spot.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Tuple

from .updater import HEARTH_VERSION


def release(version: str = HEARTH_VERSION) -> str:
    """0.7.6-preview -> 0.7.6: what the screen is keyed on."""
    m = re.match(r"\d+(?:\.\d+)*", version or "")
    return m.group(0) if m else ""


def _num(v: str) -> Tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v or ""))


def status(settings: Dict, version: str = HEARTH_VERSION) -> Tuple[Dict, Dict]:
    """What the window should do, and the settings to save for it."""
    cur = release(version)
    seen = str(settings.get("whats_new_seen") or "")
    save: Dict = {}
    if not seen:
        if not settings.get("onboarded"):
            return {"show": False, "version": cur, "from_hearth": False}, {"whats_new_seen": cur}
        # Set up before this screen existed: everyone there came from Hearth.
        save["whats_new_from_hearth"] = True
        seen = "0"
    from_hearth = bool(settings.get("whats_new_from_hearth") or save.get("whats_new_from_hearth"))
    show = _num(seen) < _num(cur)
    return {"show": show, "version": cur, "from_hearth": from_hearth}, save


MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "whats_new")


def media() -> Dict[str, str]:
    """A version for each clip and still, from its size and time, so the window
    asks for a remade file instead of the copy in its cache."""
    out: Dict[str, str] = {}
    try:
        names = os.listdir(MEDIA_DIR)
    except OSError:
        return out
    for n in names:
        try:
            st = os.stat(os.path.join(MEDIA_DIR, n))
        except OSError:
            continue
        out[n] = f"{st.st_size:x}{int(st.st_mtime):x}"
    return out


def mark_seen(version: str = HEARTH_VERSION) -> Dict:
    return {"whats_new_seen": release(version)}
