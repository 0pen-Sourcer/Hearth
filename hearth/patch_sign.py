"""Signed code patches: what a patch must carry, and checking it before it is applied.

A patch zip carries MANIFEST.json, {"version": ..., "files": {path: sha256}},
and MANIFEST.sig, an Ed25519 signature of MANIFEST.json's exact bytes by a key
in release_keys.PUBLIC_KEYS. A patch is applied only if the signature checks,
every file in the zip is listed with its hash, nothing listed is missing, and
it isn't older than the version installed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from typing import Any, Dict, List, Optional

MANIFEST, SIGNATURE = "MANIFEST.json", "MANIFEST.sig"


def _version(v: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", (v or "").split("-")[0])[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def file_hash(z: zipfile.ZipFile, name: str) -> str:
    h = hashlib.sha256()
    with z.open(name) as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest_for(z: zipfile.ZipFile, version: str) -> bytes:
    files = {n: file_hash(z, n) for n in z.namelist() if not n.endswith("/") and n not in (MANIFEST, SIGNATURE)}
    return json.dumps({"version": version, "files": dict(sorted(files.items()))}, indent=1).encode()


def sign_zip(path: str, private_raw: bytes, version: str) -> None:
    """Add MANIFEST.json and MANIFEST.sig to a patch zip (used by build_patch.py / scripts/patch_keys.py)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with zipfile.ZipFile(path) as z:
        if MANIFEST in z.namelist():
            raise ValueError("this zip is signed already")
        manifest = manifest_for(z, version)
    sig = Ed25519PrivateKey.from_private_bytes(private_raw).sign(manifest)
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, manifest)
        z.writestr(SIGNATURE, base64.b64encode(sig).decode())


def check(path: str, installed: str, keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """{ok: True, version, files} or {ok: False, error} in words a person can read."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if keys is None:
        from .release_keys import PUBLIC_KEYS as keys
    if not keys:
        return {"ok": False, "error": "this build has no release key to check updates with; install the full release"}
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if MANIFEST not in names or SIGNATURE not in names:
                return {"ok": False, "error": "this update isn't signed, so it wasn't installed"}
            manifest = z.read(MANIFEST)
            try:
                sig = base64.b64decode(z.read(SIGNATURE).strip(), validate=True)
            except ValueError:
                return {"ok": False, "error": "this update's signature is damaged, so it wasn't installed"}
            good = False
            for k in keys:
                try:
                    Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(sig, manifest)
                    good = True
                    break
                except (InvalidSignature, ValueError):
                    continue
            if not good:
                return {"ok": False, "error": "this update isn't signed by Inglen's release key, so it wasn't installed"}
            try:
                m = json.loads(manifest)
                listed = dict(m["files"])
            except (ValueError, KeyError, TypeError):
                return {"ok": False, "error": "this update's file list is damaged, so it wasn't installed"}
            present = set(names) - {MANIFEST, SIGNATURE}
            if present != set(listed):
                extra = sorted(present - set(listed))[:3]
                missing = sorted(set(listed) - present)[:3]
                return {"ok": False, "error": "this update's files don't match its signed list"
                        + (f" (not listed: {', '.join(extra)})" if extra else "")
                        + (f" (missing: {', '.join(missing)})" if missing else "") + ", so it wasn't installed"}
            for n in sorted(present):
                if file_hash(z, n) != listed[n]:
                    return {"ok": False, "error": f"{n} in this update was changed after it was signed, so it wasn't installed"}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "patch archive is corrupt; download it again"}
    if _version(str(m.get("version") or "")) < _version(installed):
        return {"ok": False, "error": f"this update ({m.get('version')}) is older than what's installed ({installed}), "
                                      "so it wasn't installed"}
    return {"ok": True, "version": m.get("version"), "files": len(present)}
