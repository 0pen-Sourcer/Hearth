"""Generate the Hearth admin-broadcast signing keypair. Run this ONCE.

    python scripts/gen_announce_key.py

- Saves the PRIVATE key to ~/.hearth/announce_private.key (base64, chmod 600).
  *** BACK THIS UP *** (password manager + an encrypted USB). If you lose it you
  can't sign NEW broadcasts until you ship a fresh public key in an app update.
  Losing it does NOT break Hearth for anyone — it only pauses your ability to
  broadcast. It is NOT recoverable; there is no reset.
- Prints the PUBLIC key. Paste it into hearth/announcements.py as
  ANNOUNCE_PUBKEY_B64 = "...", commit + push. Now only YOU (holder of the private
  key) can publish an announcement the app will trust.

To publish an announcement later:
    python scripts/sign_announcement.py   (or call announcements.sign_entry)
which signs each entry in announcements.json before you commit + push.
"""
import base64
import os
import stat
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
)

PRIV_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "announce_private.key")

if os.path.exists(PRIV_PATH):
    print(f"A key already exists at {PRIV_PATH}.")
    print("Refusing to overwrite (that would invalidate your bundled public key).")
    print("Delete it yourself only if you intend to re-key + ship a new public key.")
    sys.exit(1)

priv = Ed25519PrivateKey.generate()
priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

os.makedirs(os.path.dirname(PRIV_PATH), exist_ok=True)
with open(PRIV_PATH, "w", encoding="utf-8") as f:
    f.write(base64.b64encode(priv_raw).decode("ascii"))
try:
    os.chmod(PRIV_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600 (best-effort on Windows)
except Exception:
    pass

print("=" * 66)
print("PRIVATE key saved to:", PRIV_PATH)
print(">>> BACK IT UP NOW. It is unrecoverable if lost. <<<")
print("=" * 66)
print("\nPaste this PUBLIC key into hearth/announcements.py:\n")
print(f'ANNOUNCE_PUBKEY_B64 = "{base64.b64encode(pub_raw).decode("ascii")}"')
print("\nThen commit + push. Only your private key can sign trusted broadcasts.")
