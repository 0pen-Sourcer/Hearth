"""The installer the updater downloads is the one GitHub holds, and only that file runs.

It was checked by size alone and /api/update/launch ran any path it was given.
Now a download is checked against the sha256 GitHub publishes for the asset
(a mismatch is deleted, not kept), and launch_installer runs only an installer
this run downloaded and checked. Stand-ins for GitHub and the download.

    python tests/test_installer_integrity.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="inglen-inst-home-")
os.environ.update(HOME=HOME, USERPROFILE=HOME, HEARTH_WORKSPACE=os.path.join(HOME, "Inglen"),
                  JARVIS_WORKSPACE=os.path.join(HOME, "Inglen"), HEARTH_NO_ACTIVITY_LOG="1", HEARTH_NO_BRIDGES="1")
os.makedirs(os.environ["HEARTH_WORKSPACE"])
sys.path.insert(0, ROOT)

from hearth import llmserver, updater  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


REAL = b"MZ the real installer " * 1000
served = {"bytes": REAL}
release = {"tag_name": "v9.9.9", "assets": [
    {"name": "Inglen-Setup-9.9.9.exe", "browser_download_url": "https://github.example/i.exe", "size": len(REAL),
     "digest": "sha256:" + hashlib.sha256(REAL).hexdigest()}]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


import urllib.request  # noqa: E402
_real_open = urllib.request.urlopen
urllib.request.urlopen = lambda req, timeout=0: _Resp(json.dumps(release).encode())


def fake_download(url, dest, tmp, on_progress=None, ua="", cancel=None):
    with open(dest, "wb") as f:
        f.write(served["bytes"])


llmserver._download_with_resume = fake_download
from hearth import edition  # noqa: E402
edition.is_lite = lambda: False

ran = []
subprocess.Popen = lambda args, **kw: ran.append(args)

got = updater.download_installer()
check(f"the release's installer downloads and checks out {got}", got.get("ok"))
path = got.get("path", "")

served["bytes"] = REAL[:-1] + b"X"           # same size, one byte changed
os.remove(path)
bad = updater.download_installer()
check(f"a download that doesn't match GitHub's hash is refused {bad}", not bad.get("ok") and "doesn't match" in bad.get("error", ""))
check("and isn't kept", not os.path.exists(path))

served["bytes"] = REAL
got = updater.download_installer()
other = os.path.join(HOME, "evil.exe")
with open(other, "wb") as f:
    f.write(b"MZ something else")
r = updater.launch_installer(other, restart=False)
check(f"any other file is refused by launch_installer {r}", not r.get("ok"))
r = updater.launch_installer(got["path"], restart=False)
import time  # noqa: E402
time.sleep(0.5)
check("the checked installer runs", r.get("ok") and ran and ran[-1][0] == got["path"])

release["assets"][0].pop("digest")
os.remove(got["path"])
got = updater.download_installer()
check("a release without a hash still downloads (size checked, as before)", got.get("ok"))

urllib.request.urlopen = _real_open
print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all installer integrity checks passed")
