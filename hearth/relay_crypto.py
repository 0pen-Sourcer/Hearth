"""The encryption between a phone and this PC over Hearth's relay.

The relay server passes frames between the two and can read none of them.

Pairing. The PC has a long-lived X25519 key. To pair a phone it shows a QR code
holding the relay's address, the PC's public key and a random pairing secret;
the room the two meet in is named by a hash of those, so the server never sees
the secret. The phone makes its own long-lived key and sends its public half
with a MAC under the pairing secret, which proves it scanned the code. Both
screens then show the same six digits, derived from the shared key, for the
person to compare.

Each connection. Both sides send a fresh X25519 key. The session key is HKDF
over the long-lived shared secret and the fresh one, so only the two paired
devices can derive it, and a key leaked later doesn't open old traffic. Frames
are AES-256-GCM with a random 96-bit nonce and a per-direction counter inside,
so a replayed or reordered frame is refused.

The phone does the same with the browser's Web Crypto (X25519, HKDF-SHA256,
AES-GCM), so both sides derive the same keys.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(text: str) -> bytes:
    text = text or ""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_private() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


def raw_public(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def raw_private(priv: X25519PrivateKey) -> bytes:
    return priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())


def private_from(raw: bytes) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(raw)


def dh(priv: X25519PrivateKey, peer_public: bytes) -> bytes:
    return priv.exchange(X25519PublicKey.from_public_bytes(peer_public))


def hkdf(secret: bytes, salt: bytes, info: str, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info.encode()).derive(secret)


def room_for(pc_public: bytes, pairing_secret: bytes) -> str:
    """The room's name on the server: it can't be turned back into either input."""
    return hashlib.sha256(b"hearth-room" + pc_public + pairing_secret).hexdigest()[:32]


def pairing_payload(relay_url: str, pc_public: bytes, pairing_secret: bytes) -> Dict[str, Any]:
    return {"v": VERSION, "r": relay_url, "pc": b64(pc_public), "ps": b64(pairing_secret),
            "room": room_for(pc_public, pairing_secret)}


def phone_proof(pairing_secret: bytes, phone_public: bytes) -> str:
    """What the phone sends to show it scanned the code."""
    return b64(hmac.new(pairing_secret, b"hearth-pair" + phone_public, hashlib.sha256).digest())


def proof_ok(pairing_secret: bytes, phone_public: bytes, proof: str) -> bool:
    return hmac.compare_digest(phone_proof(pairing_secret, phone_public), proof or "")


def check_digits(static_shared: bytes, room: str) -> str:
    """The six digits both screens show while pairing."""
    n = int.from_bytes(hkdf(static_shared, room.encode(), "hearth-check", 4), "big")
    return f"{n % 1_000_000:06d}"


def session_keys(static_shared: bytes, fresh_shared: bytes, room: str, pc_eph: bytes,
                 phone_eph: bytes) -> Tuple[bytes, bytes]:
    """(pc->phone key, phone->pc key) for one connection."""
    salt = hashlib.sha256(room.encode() + pc_eph + phone_eph).digest()
    secret = static_shared + fresh_shared
    return hkdf(secret, salt, "hearth-pc-to-phone"), hkdf(secret, salt, "hearth-phone-to-pc")


def mailbox_key(static_shared: bytes, room: str) -> bytes:
    """For what waits on the server while the phone is away (and push
    notifications): sealed under the pairing alone, since the connection that
    will open it doesn't exist yet."""
    return hkdf(static_shared, room.encode(), "hearth-mailbox")


def seal_box(key: bytes, obj: Dict[str, Any]) -> Dict[str, str]:
    nonce = secrets.token_bytes(12)
    body = json.dumps({"id": b64(secrets.token_bytes(9)), "m": obj}, separators=(",", ":")).encode("utf-8")
    return {"t": "box", "iv": b64(nonce), "c": b64(AESGCM(key).encrypt(nonce, body, b"hearth-box-v1"))}


def open_box(key: bytes, frame: Dict[str, Any], seen: set) -> Optional[Dict[str, Any]]:
    """The message, or None for a forged one or one already delivered (by id)."""
    try:
        body = json.loads(AESGCM(key).decrypt(unb64(frame["iv"]), unb64(frame["c"]), b"hearth-box-v1"))
    except Exception:
        return None
    if body.get("id") in seen:
        return None
    seen.add(body.get("id"))
    return body.get("m")


class Channel:
    """One direction pair of an established connection."""

    def __init__(self, send_key: bytes, recv_key: bytes):
        self._send, self._recv = AESGCM(send_key), AESGCM(recv_key)
        self.sent, self.received = 0, 0

    def seal(self, obj: Dict[str, Any]) -> Dict[str, str]:
        self.sent += 1
        nonce = secrets.token_bytes(12)
        body = json.dumps({"n": self.sent, "m": obj}, separators=(",", ":")).encode("utf-8")
        return {"t": "msg", "iv": b64(nonce), "c": b64(self._send.encrypt(nonce, body, b"hearth-v1"))}

    def open(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The message, or None for one that is forged, replayed or out of order."""
        try:
            body = json.loads(self._recv.decrypt(unb64(frame["iv"]), unb64(frame["c"]), b"hearth-v1"))
        except Exception:
            return None
        if int(body.get("n") or 0) <= self.received:
            return None
        self.received = int(body["n"])
        return body.get("m")
