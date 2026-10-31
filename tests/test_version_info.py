"""Each built exe carries a version resource that names it Inglen, at the app's version.

Without one Windows calls the program by its file name (Hearth.exe) on the
taskbar. The file names stay Hearth*.exe for the updater.

    .\\.venv\\Scripts\\python.exe tests\\test_version_info.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import version_info as V                                  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


try:
    from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
except Exception:
    print("skip  PyInstaller is not installed")
    sys.exit(0)

src = open(os.path.join(ROOT, "hearth", "updater.py"), encoding="utf-8").read()
want = re.search(r'^HEARTH_VERSION\s*=\s*"([^"]+)"', src, re.M).group(1)
check(f"the version is the app's ({V.app_version()})", V.app_version() == want)
check("as four numbers", V.numbers("0.7.6-preview") == (0, 7, 6, 0) and V.numbers("1.2") == (1, 2, 0, 0))

out = tempfile.mkdtemp(prefix="hearth-vi-")
for exe in ("Hearth.exe", "Hearth-cli.exe", "_HearthWindow.exe"):
    path = V.write(out, exe)
    vi = load_version_info_from_text_file(path)
    table = {s.name: s.val for s in vi.kids[0].kids[0].kids}
    check(f"{exe}: parses, and says Inglen", table.get("FileDescription") == "Inglen"
          and table.get("ProductName") == "Inglen" and table.get("CompanyName") == "Inglen")
    check(f"{exe}: keeps its own file name", table.get("OriginalFilename") == exe)
    check(f"{exe}: at the app's version", table.get("ProductVersion") == want
          and vi.ffi.fileVersionMS == (V.numbers(want)[0] << 16 | V.numbers(want)[1]))
shutil.rmtree(out, ignore_errors=True)

spec = open(os.path.join(ROOT, "Hearth.spec"), encoding="utf-8").read()
for exe in ("Hearth.exe", "Hearth-cli.exe", "_HearthWindow.exe"):
    check(f"the build gives {exe} its version resource", f'version=VERSION_FOR("{exe}")' in spec)
check("and the exe names stay", 'name="Hearth",' in spec and 'name="_HearthWindow",' in spec)

print("\nall ok" if not FAILS else f"\n{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
