"""A code patch is applied only when it is signed by a release key and every
file matches its signed hash: an unsigned patch, a file changed after signing,
a signature by another key, a file slipped in, and an older version are all
refused, and refused before anything is written.

    .\\.venv\\Scripts\\python.exe tests\\test_signed_patches.py
"""
from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp(prefix="hearth-patch-")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["HEARTH_WORKSPACE"] = os.environ["JARVIS_WORKSPACE"] = os.path.join(HOME, "Jarvis")
os.environ["HEARTH_NO_ACTIVITY_LOG"] = "1"

from cryptography.hazmat.primitives import serialization as S  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from hearth import release_keys, updater  # noqa: E402
from hearth.patch_sign import sign_zip  # noqa: E402

FAILS: list = []


def check(label, cond):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        FAILS.append(label)


def raw_priv(k):
    return k.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw, S.NoEncryption())


def raw_pub(k):
    return base64.b64encode(k.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)).decode()


key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
release_keys.PUBLIC_KEYS = [raw_pub(key)]

base = os.path.join(HOME, "app")
os.makedirs(os.path.join(base, "hearth"))
with open(os.path.join(base, "hearth", "tools.py"), "w") as f:
    f.write("OLD = True\n")
updater.code_dir = lambda: base
updater._sync_registry_version = lambda: None


def patch(name, files, sign_with=None, version=updater.HEARTH_VERSION):
    p = os.path.join(HOME, name)
    with zipfile.ZipFile(p, "w") as z:
        for n, body in files.items():
            z.writestr(n, body)
    if sign_with is not None:
        sign_zip(p, raw_priv(sign_with), version)
    return p


def installed():
    with open(os.path.join(base, "hearth", "tools.py")) as f:
        return f.read()


new = {"hearth/tools.py": "NEW = True\n", "hearth/extra.py": "X = 1\n"}

r = updater.apply_patch(patch("unsigned.zip", new))
check(f"an unsigned patch is refused ({r.get('error')})", not r["ok"] and "isn't signed" in r["error"] and installed() == "OLD = True\n")

r = updater.apply_patch(patch("other.zip", new, sign_with=other))
check(f"one signed by another key is refused ({r.get('error')})", not r["ok"] and "release key" in r["error"]
      and installed() == "OLD = True\n")

good = patch("good.zip", new, sign_with=key)
tampered = os.path.join(HOME, "tampered.zip")
with zipfile.ZipFile(good) as src, zipfile.ZipFile(tampered, "w") as dst:
    for n in src.namelist():
        dst.writestr(n, "import os; os.system('evil')\n" if n == "hearth/tools.py" else src.read(n))
r = updater.apply_patch(tampered)
check(f"a file changed after signing is refused ({r.get('error')})", not r["ok"] and "changed after it was signed" in r["error"]
      and installed() == "OLD = True\n")

slipped = os.path.join(HOME, "slipped.zip")
shutil.copy(good, slipped)
with zipfile.ZipFile(slipped, "a") as z:
    z.writestr("hearth/backdoor.py", "pass\n")
r = updater.apply_patch(slipped)
check(f"a file slipped in beside the signed ones is refused ({r.get('error')})", not r["ok"] and "backdoor.py" in r["error"]
      and not os.path.exists(os.path.join(base, "hearth", "backdoor.py")))

r = updater.apply_patch(patch("old.zip", new, sign_with=key, version="0.0.1"))
check(f"an older version is refused, even signed ({r.get('error')})", not r["ok"] and "older" in r["error"])

r = updater.apply_patch(good)
check(f"a signed, untouched patch is applied ({r})", r["ok"] and installed() == "NEW = True\n"
      and not os.path.exists(os.path.join(base, "MANIFEST.json")))

release_keys.PUBLIC_KEYS = [raw_pub(other), raw_pub(key)]
r = updater.apply_patch(patch("rotated.zip", new, sign_with=key))
check("with two keys listed (rotation), either signs", r["ok"])
release_keys.PUBLIC_KEYS = []
r = updater.apply_patch(patch("nokeys.zip", new, sign_with=key))
check(f"a build with no release key refuses every patch ({r.get('error')})", not r["ok"])

print()
if FAILS:
    print(f"{len(FAILS)} check(s) failed")
    sys.exit(1)
print("all signed patch checks passed")
