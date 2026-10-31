"""The Windows version resource for each exe, written at build time.

Windows names a running program after its FileDescription (taskbar, Task Manager,
file properties), so each exe says Inglen and the app's own version, read from
hearth/updater.py. The file names stay Hearth*.exe: updates look for them.
"""
from __future__ import annotations

import os
import re
from typing import Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "Inglen"


def app_version(root: str = ROOT) -> str:
    """HEARTH_VERSION from hearth/updater.py, read as text so a build doesn't import the app."""
    with open(os.path.join(root, "hearth", "updater.py"), encoding="utf-8") as f:
        m = re.search(r'^HEARTH_VERSION\s*=\s*["\']([^"\']+)["\']', f.read(), re.M)
    if not m:
        raise ValueError("HEARTH_VERSION not found in hearth/updater.py")
    return m.group(1)


def numbers(version: str) -> Tuple[int, int, int, int]:
    """0.7.6-preview -> (0, 7, 6, 0): the four numbers a version resource holds."""
    nums = [int(n) for n in re.findall(r"\d+", version.split("-")[0])][:4]
    return tuple(nums + [0] * (4 - len(nums)))  # type: ignore[return-value]


def render(version: str, exe: str) -> str:
    """A PyInstaller version file for `exe` (its file name, e.g. Hearth.exe)."""
    n = numbers(version)
    dotted = ".".join(map(str, n))
    internal = os.path.splitext(exe)[0]
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={n}, prodvers={n}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', {NAME!r}),
        StringStruct('FileDescription', {NAME!r}),
        StringStruct('FileVersion', {dotted!r}),
        StringStruct('InternalName', {internal!r}),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', {exe!r}),
        StringStruct('ProductName', {NAME!r}),
        StringStruct('ProductVersion', {version!r}),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def write(out_dir: str, exe: str, root: str = ROOT) -> str:
    """Write `exe`'s version file into out_dir; its path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "version_" + os.path.splitext(exe)[0] + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(app_version(root), exe))
    return path
