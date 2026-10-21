"""The apps a file can be opened with, as Windows itself offers them.

The Files tab's Open menu lists them: the default app first, then what the
file type's own Open-with lists name. Nothing here names an app; the lists are
the ones Windows keeps for each file type.
"""
from __future__ import annotations

import ctypes
import io
import os
import subprocess
import sys
from typing import Dict, List, Optional

_ASSOCSTR_EXECUTABLE = 2
_ASSOCSTR_FRIENDLYAPPNAME = 4


def _assoc(assoc: str, what: int, verb: Optional[str] = "open") -> str:
    if sys.platform != "win32":
        return ""
    try:
        size = ctypes.c_ulong(0)
        f = ctypes.windll.shlwapi.AssocQueryStringW
        f(0, what, assoc, verb, None, ctypes.byref(size))
        if not size.value:
            return ""
        buf = ctypes.create_unicode_buffer(size.value)
        if f(0, what, assoc, verb, buf, ctypes.byref(size)) != 0:
            return ""
        return buf.value
    except Exception:
        return ""


def _name_of(exe: str) -> str:
    try:
        import win32api  # type: ignore
        lang, cp = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
        desc = win32api.GetFileVersionInfo(exe, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription")
        if desc and desc.strip():
            return desc.strip()
    except Exception:
        pass
    return os.path.splitext(os.path.basename(exe))[0]


def _values(root, sub: str) -> List[str]:
    import winreg
    out = []
    try:
        with winreg.OpenKey(root, sub) as k:
            i = 0
            while True:
                try:
                    name, val, _t = winreg.EnumValue(k, i)
                except OSError:
                    break
                out.append(name if name else str(val))
                i += 1
    except OSError:
        pass
    return out


def _subkeys(root, sub: str) -> List[str]:
    import winreg
    out = []
    try:
        with winreg.OpenKey(root, sub) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


def openers(path: str) -> List[Dict[str, str]]:
    """[{exe, name, default}] for this file, the default app first."""
    if sys.platform != "win32":
        return []
    import winreg
    ext = os.path.splitext(path)[1].lower()
    if not ext:
        return []
    seen, out = set(), []

    def add(exe: str, name: str = "", default: bool = False):
        exe = os.path.expandvars((exe or "").strip().strip('"'))
        key = exe.lower()
        if not exe or key in seen or not os.path.isfile(exe) or os.path.basename(key) in ("openwith.exe", "rundll32.exe"):
            return
        seen.add(key)
        out.append({"exe": exe, "name": name or _name_of(exe), "default": default})

    add(_assoc(ext, _ASSOCSTR_EXECUTABLE), _assoc(ext, _ASSOCSTR_FRIENDLYAPPNAME), default=True)
    progids = _values(winreg.HKEY_CLASSES_ROOT, ext + r"\OpenWithProgids")
    progids += _values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts" + "\\" + ext + r"\OpenWithProgids")
    for pid in progids:
        add(_assoc(pid, _ASSOCSTR_EXECUTABLE), _assoc(pid, _ASSOCSTR_FRIENDLYAPPNAME))
    apps = [v for v in _values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts" + "\\" + ext + r"\OpenWithList")]
    apps += _subkeys(winreg.HKEY_CLASSES_ROOT, ext + r"\OpenWithList")
    for app in apps:
        if not app.lower().endswith(".exe"):
            continue
        add(_assoc("Applications\\" + app, _ASSOCSTR_EXECUTABLE))
    return out[:12]


def open_with(path: str, exe: str) -> None:
    """Open the file in one of the apps openers() named for it."""
    if not any(o["exe"].lower() == (exe or "").lower() for o in openers(path)):
        raise ValueError("not an app this file opens with")
    subprocess.Popen([exe, path], close_fds=True)


def icon_png(exe: str, size: int = 32) -> Optional[bytes]:
    """The app's own icon as PNG bytes, or None."""
    if sys.platform != "win32" or not os.path.isfile(exe):
        return None
    try:
        import win32gui, win32ui, win32con  # type: ignore
        from PIL import Image  # type: ignore
        large, small = win32gui.ExtractIconEx(exe, 0, 1)
        icons = large or small
        if not icons:
            return None
        hicon = icons[0]
        dc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        mem = dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(dc, 32, 32)
        mem.SelectObject(bmp)
        mem.FillSolidRect((0, 0, 32, 32), 0x00FF00FF)
        win32gui.DrawIconEx(mem.GetHandleOutput(), 0, 0, hicon, 32, 32, 0, None, win32con.DI_NORMAL)
        raw = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGBA", (32, 32), raw, "raw", "BGRA", 0, 1)
        px = img.load()
        for y in range(32):
            for x in range(32):
                r, g, b, a = px[x, y]
                px[x, y] = (r, g, b, 0) if (r, g, b) == (255, 0, 255) else (r, g, b, 255)
        for h in list(large) + list(small):
            win32gui.DestroyIcon(h)
        mem.DeleteDC()
        if size != 32:
            img = img.resize((size, size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None
