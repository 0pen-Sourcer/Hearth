"""The apps a Linux desktop knows about, from its own .desktop files.

Every desktop (GNOME, KDE, Cinnamon, XFCE) lists installed apps as .desktop
files in the XDG data folders, Flatpak and Snap ones included. They give each
app's name, how to start it, its icon and the file types it opens. Opening an
app by name and the Files tab's Open with menu read them; nothing here names an
app.
"""
from __future__ import annotations

import mimetypes
import os
import shlex
import shutil
import subprocess
from typing import Dict, List, Optional

_CACHE: Dict[str, object] = {}


def _dirs() -> List[str]:
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    data = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    extra = ["/var/lib/flatpak/exports/share", os.path.expanduser("~/.local/share/flatpak/exports/share"),
             "/var/lib/snapd/desktop"]
    out = []
    for d in [home] + data + extra:
        a = os.path.join(d, "applications")
        if d and a not in out and os.path.isdir(a):
            out.append(a)
    return out


def _parse(path: str) -> Optional[Dict[str, object]]:
    fields: Dict[str, str] = {}
    section = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("["):
                    section = line
                    continue
                if section != "[Desktop Entry]" or "=" not in line or line.startswith("#"):
                    continue
                k, _, v = line.partition("=")
                fields.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    if fields.get("Type", "Application") != "Application" or not fields.get("Exec"):
        return None
    if fields.get("Hidden", "").lower() == "true":
        return None
    return {"id": os.path.basename(path), "path": path, "name": fields.get("Name") or os.path.basename(path)[:-8],
            "exec": fields["Exec"], "icon": fields.get("Icon", ""),
            "shown": fields.get("NoDisplay", "").lower() != "true",
            "mime": [m for m in fields.get("MimeType", "").split(";") if m]}


def entries() -> List[Dict[str, object]]:
    """Every app entry, the user's own overriding the system's of the same id."""
    dirs = _dirs()
    stamp = tuple((d, os.stat(d).st_mtime) for d in dirs)
    if _CACHE.get("stamp") == stamp:
        return _CACHE["list"]  # type: ignore[return-value]
    seen, out = set(), []
    for d in dirs:
        for root, _sub, files in os.walk(d):
            for fn in sorted(files):
                if not fn.endswith(".desktop") or fn in seen:
                    continue
                e = _parse(os.path.join(root, fn))
                if e:
                    seen.add(fn)
                    out.append(e)
    _CACHE.update(stamp=stamp, list=out)
    return out


def find_app(name: str) -> Optional[Dict[str, object]]:
    """The app someone means by a name: its listed name or file name, exactly,
    then one that starts with it, then one that contains it."""
    want = (name or "").strip().lower()
    if not want:
        return None
    apps = [e for e in entries() if e["shown"]]
    keys = lambda e: (str(e["name"]).lower(), str(e["id"])[:-8].lower().rsplit(".", 1)[-1])
    for test in (lambda k: want in k,
                 lambda k: any(x.startswith(want) for x in k),
                 lambda k: any(want in x for x in k)):
        hit = next((e for e in apps if test(keys(e))), None)
        if hit:
            return hit
    return None


def argv(entry: Dict[str, object], files: Optional[List[str]] = None) -> List[str]:
    """The entry's Exec line as arguments, its file codes filled in."""
    files = list(files or [])
    out: List[str] = []
    for part in shlex.split(str(entry["exec"])):
        if part in ("%f", "%u"):
            out += files[:1]
        elif part in ("%F", "%U"):
            out += files
        elif part in ("%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"):
            continue
        else:
            out.append(part.replace("%%", "%"))
    if files and not any(f in out for f in files):
        out += files
    return out


def launch(entry: Dict[str, object], files: Optional[List[str]] = None) -> int:
    proc = subprocess.Popen(argv(entry, files), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True)
    return proc.pid


def mime_of(path: str) -> str:
    tool = shutil.which("xdg-mime")
    if tool:
        try:
            r = subprocess.run([tool, "query", "filetype", path], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return mimetypes.guess_type(path)[0] or ""


def default_for(mime: str) -> str:
    tool = shutil.which("xdg-mime")
    if not tool or not mime:
        return ""
    try:
        r = subprocess.run([tool, "query", "default", mime], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def apps_for(path: str) -> List[Dict[str, object]]:
    """Apps that open this kind of file, the default first."""
    mime = mime_of(path)
    if not mime:
        return []
    default = default_for(mime)
    hits = [e for e in entries() if mime in e["mime"]]
    hits.sort(key=lambda e: (e["id"] != default, str(e["name"]).lower()))
    return hits


def icon_file(entry: Dict[str, object], size: int = 32) -> Optional[str]:
    """A PNG of the entry's icon from the icon themes, nearest to `size`."""
    icon = str(entry.get("icon") or "")
    if not icon:
        return None
    if os.path.isabs(icon):
        return icon if icon.endswith(".png") and os.path.isfile(icon) else None
    best, best_d = None, 10 ** 6
    for base in [os.path.expanduser("~/.local/share/icons"), os.path.expanduser("~/.icons")] + \
            [os.path.join(d, "icons") for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]:
        theme = os.path.join(base, "hicolor")
        if not os.path.isdir(theme):
            continue
        for sz in os.listdir(theme):
            p = os.path.join(theme, sz, "apps", icon + ".png")
            if os.path.isfile(p):
                try:
                    d = abs(int(sz.split("x")[0]) - size)
                except ValueError:
                    d = 999
                if d < best_d:
                    best, best_d = p, d
    if best:
        return best
    p = os.path.join("/usr/share/pixmaps", icon + ".png")
    return p if os.path.isfile(p) else None
