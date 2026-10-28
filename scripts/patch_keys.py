"""Make the release key pair, or sign a code patch with it.

    python scripts/patch_keys.py new <path-outside-the-repo>   writes the private key there, prints the public key
                                                               to add to hearth/release_keys.py
    python scripts/patch_keys.py sign <patch.zip> <key-file> [version]

build_patch.py signs on its own when HEARTH_PATCH_KEY names the key file. Keep the key file off this repository
and backed up: without it no release can update installs that trust it (list a second key before retiring one).
"""
from __future__ import annotations

import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv) -> int:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if len(argv) >= 2 and argv[0] == "new":
        out = os.path.abspath(argv[1])
        if out.startswith(ROOT + os.sep):
            print("refusing: keep the private key outside the repository", file=sys.stderr)
            return 2
        if os.path.exists(out):
            print(f"refusing: {out} exists", file=sys.stderr)
            return 2
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())
        with open(out, "w") as f:
            f.write(base64.b64encode(raw).decode())
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        pub = key.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
        print(f"private key written to {out}")
        print(f"public key (add to hearth/release_keys.py PUBLIC_KEYS): {base64.b64encode(pub).decode()}")
        return 0
    if len(argv) >= 3 and argv[0] == "sign":
        from hearth.patch_sign import sign_zip
        from hearth.updater import HEARTH_VERSION
        with open(argv[2]) as f:
            raw = base64.b64decode(f.read().strip())
        sign_zip(argv[1], raw, argv[3] if len(argv) > 3 else HEARTH_VERSION)
        print(f"signed {argv[1]}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
