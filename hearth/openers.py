"""The apps a file can be opened with, as the system itself offers them.

The Files tab's Open menu lists them: the default app first, then what the
file type's own Open-with lists name. Nothing here names an app; the lists are
the ones Windows keeps for each file type, or a Linux desktop's app entries.
On Linux an app is named by its .desktop file where Windows gives an exe.
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
    if sys.platform.startswith("linux"):
        from . import desktop_entries as _de
        apps = _de.apps_for(path)
        default = _de.default_for(_de.mime_of(path))
        return [{"exe": str(e["path"]), "name": str(e["name"]), "default": e["id"] == default}
                for e in apps[:12]]
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
    if exe.endswith(".desktop"):
        from . import desktop_entries as _de
        entry = next(e for e in _de.entries() if e["path"] == exe)
        _de.launch(entry, [path])
        return
    subprocess.Popen([exe, path], close_fds=True)


def icon_png(exe: str, size: int = 32) -> Optional[bytes]:
    """The app's own icon as PNG bytes, or None."""
    if exe.endswith(".desktop"):
        try:
            from PIL import Image  # type: ignore
            from . import desktop_entries as _de
            entry = next((e for e in _de.entries() if e["path"] == exe), None)
            src = _de.icon_file(entry, size) if entry else None
            if not src:
                return None
            img = Image.open(src).convert("RGBA").resize((size, size), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
        except Exception:
            return None
    if sys.platform != "win32" or not os.path.isfile(exe):
        return None
    try:
        return _win_icon_png(exe, size)
    except Exception:
        return None


def _win_icon_png(exe: str, size: int) -> Optional[bytes]:
    """The exe's icon, taken at 64 px and drawn once on black and once on
    white: the difference is its real transparency, so soft edges stay soft
    (keying out one colour left them jagged and fringed)."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image  # type: ignore
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    big = 64
    hicon = wintypes.HICON()
    got = u.PrivateExtractIconsW(exe, 0, big, big, ctypes.byref(hicon), None, 1, 0)
    if got != 1 or not hicon:
        return None

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    def draw(fill: int) -> bytes:
        bmi = BMI()
        bmi.biSize, bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = ctypes.sizeof(BMI), big, -big, 1, 32
        bits = ctypes.c_void_p()
        sdc = u.GetDC(None)
        mdc = g.CreateCompatibleDC(sdc)
        dib = g.CreateDIBSection(sdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        old = g.SelectObject(mdc, dib)
        ctypes.memset(bits, fill, big * big * 4)
        u.DrawIconEx(mdc, 0, 0, hicon, big, big, 0, None, 3)          # DI_NORMAL
        data = ctypes.string_at(bits, big * big * 4)
        g.SelectObject(mdc, old)
        g.DeleteObject(dib)
        g.DeleteDC(mdc)
        u.ReleaseDC(None, sdc)
        return data

    try:
        on_black, on_white = draw(0), draw(255)
    finally:
        u.DestroyIcon(hicon)
    img = Image.new("RGBA", (big, big))
    px = []
    for i in range(0, len(on_black), 4):
        b0, g0, r0 = on_black[i], on_black[i + 1], on_black[i + 2]
        b1, g1, r1 = on_white[i], on_white[i + 1], on_white[i + 2]
        a = 255 - max(r1 - r0, g1 - g0, b1 - b0)
        if a <= 0:
            px.append((0, 0, 0, 0))
        else:
            k = 255.0 / a
            px.append((min(255, int(r0 * k)), min(255, int(g0 * k)), min(255, int(b0 * k)), a))
    img.putdata(px)
    if size != big:
        img = img.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
