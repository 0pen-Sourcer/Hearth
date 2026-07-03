"""Broadcast announcements to every Hearth build.

Each client polls a small public JSON feed (default: the repo's raw
`announcements.json` on GitHub) and shows any announcement it hasn't seen yet
as a toast (GUI) or a printed banner (CLI). Dedup is by announcement id, stored
in ~/.hearth/seen_announcements.json, so each fires exactly once per machine.

The author publishes by adding an entry to `announcements.json` in the repo:
`publish()` (and the GUI Broadcast panel) write it locally, then you commit +
push. Clients poll the raw URL and surface it. No server, no account, no
per-user opt-in - if you push it, every build that opens sees it.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import uuid
from urllib.request import urlopen, Request

REPO = os.environ.get("HEARTH_ANNOUNCE_REPO", "0pen-sourcer/hearth")
FEED_URL = os.environ.get(
    "HEARTH_ANNOUNCE_URL",
    f"https://raw.githubusercontent.com/{REPO}/main/announcements.json",
)
_SEEN_PATH = os.path.join(os.path.expanduser("~"), ".hearth", "seen_announcements.json")
# The local feed the author edits, then commits + pushes (repo root).
_LOCAL_FEED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "announcements.json"
)


def _load_seen() -> set:
    try:
        with open(_SEEN_PATH, encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def _save_seen(seen: set) -> None:
    try:
        os.makedirs(os.path.dirname(_SEEN_PATH), exist_ok=True)
        with open(_SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(seen), f)
    except Exception:
        pass


def _fetch(url: str, timeout: float = 6.0):
    req = Request(url, headers={"User-Agent": "Hearth-Announce"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except ssl.SSLError:
        # SSL-inspecting networks (corporate AV / proxy) break cert chains. This
        # is a public, read-only feed, so retry unverified rather than fail.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))


# ed25519 public key; private key never in repo. Empty = no trusted feed.
ANNOUNCE_PUBKEY_B64 = ""


def _canonical(entry: dict) -> bytes:
    e = {k: v for k, v in entry.items() if k != "sig"}
    return json.dumps(e, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify(entry: dict) -> bool:
    if not ANNOUNCE_PUBKEY_B64 or not entry.get("sig"):
        return False
    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(ANNOUNCE_PUBKEY_B64))
        pub.verify(base64.b64decode(entry["sig"]), _canonical(entry))
        return True
    except Exception:
        return False


def sign_entry(entry: dict, private_key_path: str) -> dict:
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with open(private_key_path, "rb") as f:
        priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(f.read().strip()))
    e = {k: v for k, v in entry.items() if k != "sig"}
    e["sig"] = base64.b64encode(priv.sign(_canonical(e))).decode("ascii")
    return e


def _entries(data) -> list:
    if isinstance(data, dict):
        data = data.get("announcements") or data.get("items") or []
    return [e for e in data if isinstance(e, dict) and e.get("id") and _verify(e)]


def _read_local_feed() -> list:
    try:
        with open(_LOCAL_FEED, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d.get("announcements") or []
        if isinstance(d, list):
            return d
    except Exception:
        pass
    return []


def fetch_new(url: str = FEED_URL, mark: bool = True) -> list:
    """Announcements not yet seen on this machine. Best-effort: any network or
    parse failure returns []. Marks the returned ones as seen so they fire once."""
    try:
        data = _fetch(url)
    except Exception:
        return []
    seen = _load_seen()
    new = [e for e in _entries(data) if str(e["id"]) not in seen]
    if mark and new:
        seen |= {str(e["id"]) for e in new}
        _save_seen(seen)
    return new


def mark_all_seen(url: str = FEED_URL) -> None:
    """Suppress the current backlog without showing it (used on a fresh install
    so a new user isn't hit with months of old announcements at once)."""
    try:
        data = _fetch(url)
    except Exception:
        return
    seen = _load_seen() | {str(e["id"]) for e in _entries(data)}
    _save_seen(seen)


def publish(title: str, body: str, kind: str = "info") -> dict:
    """Append an announcement to the LOCAL announcements.json (repo root). The
    author then commits + pushes it; every build picks it up on next poll."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "title": (title or "").strip(),
        "body": (body or "").strip(),
        "kind": kind,
        "created": int(time.time()),
    }
    feed = _read_local_feed()
    feed.append(entry)
    with open(_LOCAL_FEED, "w", encoding="utf-8") as f:
        json.dump({"announcements": feed}, f, indent=2)
    return {
        "ok": True,
        "entry": entry,
        "path": _LOCAL_FEED,
        "next": "commit + push announcements.json, then every Hearth build sees it on next open",
    }
