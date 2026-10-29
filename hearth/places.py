"""The places the Files tab opens on: the person's own folders and what Inglen touched.

Desktop, Documents and Downloads are found where the system keeps them (Windows
known folders, which may live in OneDrive; XDG user dirs on Linux), not guessed
from the home folder. Inglen's own folder (the workspace) is one place among
them. "Recent" is the files Inglen wrote, changed or moved lately, newest first.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, List, Optional


def _windows_known(guid: str) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
        h = guid.strip("{}").split("-")
        g = GUID(int(h[0], 16), int(h[1], 16), int(h[2], 16),
                 (ctypes.c_ubyte * 8)(*bytes.fromhex(h[3] + h[4])))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return ""
        path = out.value or ""
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    except Exception:
        return ""


_WIN = {"desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
        "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}"}
_XDG = {"desktop": "DESKTOP", "documents": "DOCUMENTS", "downloads": "DOWNLOAD"}
_FALLBACK = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads"}


def _xdg(name: str) -> str:
    home = os.path.expanduser("~")
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "user-dirs.dirs")
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                m = re.match(rf'\s*XDG_{name}_DIR="(.*)"', line)
                if m:
                    return m.group(1).replace("$HOME", home)
    except OSError:
        pass
    return ""


def known_folder(kind: str) -> str:
    """Where this person's Desktop, Documents or Downloads is ('' when it isn't there)."""
    path = ""
    if sys.platform == "win32":
        path = _windows_known(_WIN[kind])
    elif sys.platform.startswith("linux"):
        path = _xdg(_XDG[kind])
    if not path:
        path = os.path.join(os.path.expanduser("~"), _FALLBACK[kind])
    # A user dir pointing at home itself (XDG does this when it's unset) isn't a place of its own.
    if not os.path.isdir(path) or os.path.realpath(path) == os.path.realpath(os.path.expanduser("~")):
        return ""
    return path


def roots() -> Dict[str, str]:
    """place id -> folder, for the places that exist here."""
    from .tools import WORKSPACE
    out = {}
    for kind in ("desktop", "documents", "downloads"):
        p = known_folder(kind)
        if p:
            out[kind] = p
    out["inglen"] = WORKSPACE
    return out


LABELS = {"recent": "Recent", "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
          "inglen": "Inglen"}


def listing() -> List[Dict[str, str]]:
    r = roots()
    return [{"id": "recent", "label": LABELS["recent"], "path": ""}] + \
        [{"id": k, "label": LABELS[k], "path": v} for k, v in r.items()]


def root_for(place: str) -> Optional[str]:
    return roots().get(place)


def touched(limit: int = 60) -> List[Dict]:
    """Files Inglen wrote, changed or moved lately, newest first, each once, still there."""
    from . import undo
    try:
        changes = undo._read().get("changes") or []
    except Exception:
        changes = []
    seen, out = set(), []
    for ch in reversed(changes):
        if ch.get("undone"):
            continue
        for it in ch.get("items") or []:
            kind = it.get("kind")
            path = it.get("dest") if kind == "move" else it.get("path") if kind == "change" else None
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            out.append({"name": os.path.basename(path), "path": path, "folder": os.path.dirname(path),
                        "is_dir": False, "size": st.st_size, "mtime": st.st_mtime})
            if len(out) >= limit:
                return out
    return out
