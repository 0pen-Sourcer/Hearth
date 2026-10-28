"""An update that is cut off partway through.

A patch replaces the code files one at a time. The rollback inside the applier
only runs for a failure the applier lives to see, so losing the process during
the extract, a power cut, a kill, a reboot, left files from two versions mixed
together with nothing to notice it and nothing to reverse it. The install did
not come back up and there was no way back short of reinstalling.

    .\\.venv\\Scripts\\python.exe tests\\test_patch_recovery.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hearth import updater                                          # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def install(files: dict) -> Path:
    """A stand-in for a patchable install: a directory of code files."""
    base = Path(tempfile.mkdtemp())
    for name, body in files.items():
        p = base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return base


# Patches are applied only when signed by a release key (test_signed_patches.py): sign with a key made here.
from cryptography.hazmat.primitives import serialization as _S                   # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
import base64 as _b64                                                            # noqa: E402
from hearth import release_keys as _rk                                           # noqa: E402
from hearth.patch_sign import sign_zip as _sign                                  # noqa: E402
_KEY = Ed25519PrivateKey.generate()
_rk.PUBLIC_KEYS = [_b64.b64encode(_KEY.public_key().public_bytes(_S.Encoding.Raw, _S.PublicFormat.Raw)).decode()]


def patch_zip(files: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    _sign(path, _KEY.private_bytes(_S.Encoding.Raw, _S.PrivateFormat.Raw, _S.NoEncryption()), updater.HEARTH_VERSION)
    return path


OLD = {"hearth/__init__.py": "VERSION = 'old'\n",
       "hearth/tools.py": "TOOLS = 'old'\n"}
NEW = {"hearth/__init__.py": "VERSION = 'new'\n",
       "hearth/tools.py": "TOOLS = 'new'\n",
       "hearth/brand_new.py": "ADDED = True\n"}

_real_code_dir = updater.code_dir

# ------------------------------------------------------- a patch that finishes
base = install(OLD)
updater.code_dir = lambda: str(base)
r = updater.apply_patch(patch_zip(NEW))
check("a clean patch applies", r.get("ok") and r.get("files") == 3)
check("and it leaves the new files in place",
      (base / "hearth" / "tools.py").read_text() == "TOOLS = 'new'\n")
check("no marker is left behind once it finishes",
      not (base / updater._INFLIGHT).exists())
check("and a start afterwards finds nothing to undo",
      updater.recover_interrupted_patch().get("recovered") is False)

# ----------------------------------------------- a patch killed mid-extract
# Exactly what is on disk when the process dies between two files: the first
# one is the new version, the second is still the old one, an added file may or
# may not be there, and the marker the applier wrote is still sitting there.
base = install(OLD)
updater.code_dir = lambda: str(base)
backup = Path(updater._backup_dir(str(base)))
for name, body in OLD.items():
    p = backup / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
(base / updater._INFLIGHT).write_text(json.dumps({
    "replaced": ["hearth/__init__.py", "hearth/tools.py"],
    "added": ["hearth/brand_new.py"]}), encoding="utf-8")
(base / "hearth" / "__init__.py").write_text("VERSION = 'new'\n", encoding="utf-8")
(base / "hearth" / "brand_new.py").write_text("ADDED = True\n", encoding="utf-8")

check("the mixed state is what we are starting from",
      (base / "hearth" / "__init__.py").read_text() == "VERSION = 'new'\n"
      and (base / "hearth" / "tools.py").read_text() == "TOOLS = 'old'\n")

r = updater.recover_interrupted_patch()
check("the interrupted update is noticed", r.get("recovered") is True)
check("the half-written file goes back to the version that worked",
      (base / "hearth" / "__init__.py").read_text() == "VERSION = 'old'\n")
check("the file it never reached is untouched",
      (base / "hearth" / "tools.py").read_text() == "TOOLS = 'old'\n")
check("a file the patch was adding, which has no earlier version, is removed",
      not (base / "hearth" / "brand_new.py").exists())
check("the marker is cleared, so the next start is an ordinary one",
      not (base / updater._INFLIGHT).exists())
check("and that next start finds nothing to do",
      updater.recover_interrupted_patch().get("recovered") is False)

# ------------------------------------------- nothing to recover, nothing done
base = install(OLD)
updater.code_dir = lambda: str(base)
check("an install that was never patched is left alone",
      updater.recover_interrupted_patch().get("recovered") is False)
check("and its files are not touched",
      (base / "hearth" / "tools.py").read_text() == "TOOLS = 'old'\n")

updater.code_dir = lambda: None
check("a build that cannot be patched has nothing to recover",
      updater.recover_interrupted_patch().get("recovered") is False)

updater.code_dir = _real_code_dir

# ------------------------------------------------- it runs before anything else
_init = io.open(os.path.join(ROOT, "hearth", "__init__.py"), encoding="utf-8").read()
_call = _init.find("recover_interrupted_patch")
check("the package recovers on import", _call > 0)
# It has to come before the submodules it would be restoring.
_first_sub = min((_init.find(m) for m in ("from .tools", "from .persona", "from .memory")
                  if _init.find(m) > 0), default=len(_init))
check("and it does so before it loads the files it may be replacing",
      _call < _first_sub)

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all patch recovery checks passed")
