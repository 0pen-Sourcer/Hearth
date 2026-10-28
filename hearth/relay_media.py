"""Files for the phone, kept on the PC and fetched when opened.

A file the agent sends or names goes to the phone as a small card: its name,
size, type and, for a picture, a thumbnail of a few KB. The phone asks for the
whole file only when the person opens it, in parts over the live connection;
nothing big waits in the relay's mailbox, where it used to push out the
person's own messages.

The phone can fetch only what the PC carded itself: each card names an id
made from the file's path with a secret kept on this PC, so a phone can't ask
for a path, or guess the id of a file it wasn't shown. The ids are kept in
~/.hearth/relay_media.json so a chat reopened later still opens its files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional

STORE = os.path.join(os.path.expanduser("~"), ".hearth", "relay_media.json")
MAX_FILE = 100 * 1024 * 1024       # what a phone can open from the PC
CHUNK = 96 * 1024                  # raw bytes per part: one relay frame once encoded
KEEP = 3000                        # cards remembered
THUMB_PX = 360
_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {}


def _load() -> Dict[str, Any]:
    if _STATE:
        return _STATE
    try:
        with open(STORE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict) or not d.get("secret"):
        d = {"secret": secrets.token_hex(32), "files": {}}
    d.setdefault("files", {})
    _STATE.update(d)
    return _STATE


def _save() -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_STATE, f)
    os.replace(tmp, STORE)


def _id_for(path: str) -> str:
    key = os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace")
    return hmac.new(bytes.fromhex(_load()["secret"]), key, hashlib.sha256).hexdigest()[:32]


def kind_of(name: str) -> str:
    mime = mimetypes.guess_type(name)[0] or ""
    if mime.startswith(("image/", "video/", "audio/")):
        return mime.split("/")[0]
    return "pdf" if mime == "application/pdf" else "file"


def _thumb(path: str) -> str:
    """A small JPEG of a picture, as a data URL; '' when it isn't one Pillow can read."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.thumbnail((THUMB_PX, THUMB_PX))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def card(path: str) -> Optional[Dict[str, Any]]:
    """The card for a file on this PC, registered so the phone can open it; None when it can't be."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path) or st.st_size > MAX_FILE:
        return None
    name = os.path.basename(path)
    mid = _id_for(path)
    with _LOCK:
        files = _load()["files"]
        files[mid] = {"path": os.path.abspath(path), "at": time.time()}
        if len(files) > KEEP:
            for k in sorted(files, key=lambda k: files[k].get("at", 0))[:len(files) - KEEP]:
                files.pop(k, None)
        _save()
    kind = kind_of(name)
    return {"media": mid, "name": name, "size": st.st_size, "mime": mimetypes.guess_type(name)[0] or "",
            "kind": kind, "thumb": _thumb(path) if kind == "image" else ""}


def chunk(mid: str, offset: int = 0, length: int = CHUNK) -> Dict[str, Any]:
    """One part of a carded file: {data (base64url), offset, size, done} or {error}."""
    with _LOCK:
        rec = _load()["files"].get(str(mid or ""))
    if not rec:
        return {"error": "that file isn't one this PC sent"}
    path = rec["path"]
    try:
        size = os.path.getsize(path)
        offset = max(0, int(offset or 0))
        length = max(1, min(int(length or CHUNK), CHUNK))
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(length)
    except (OSError, ValueError):
        return {"error": "the file isn't on the PC any more"}
    data = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"media": mid, "offset": offset, "size": size, "data": data, "done": offset + len(raw) >= size}
