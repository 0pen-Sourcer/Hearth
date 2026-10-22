"""Hearth Plus: the licence on this PC.

Plus is sold through Dodo Payments as a subscription with a licence key. A key
is activated once per PC (Dodo counts activations), checked again now and then,
and remembered in ~/.hearth/plus.json. Dodo's licence endpoints are public, so
no secret lives in the app; the relay server keeps the real subscription state
from Dodo's webhooks and is what actually lets a lapsed account go.

A key that can't be checked (offline) stays good for a grace period after its
last successful check, so a trip without internet doesn't switch Plus off.
"""
from __future__ import annotations

import json
import os
import platform
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

LIVE = "https://live.dodopayments.com"
TEST = "https://test.dodopayments.com"
CHECK_EVERY_S = 24 * 3600
GRACE_S = 7 * 24 * 3600
# Where the Buy button goes: the Plus product's checkout link on Dodo.
CHECKOUT_URL = os.environ.get("HEARTH_PLUS_CHECKOUT") or ""


def _setting(key: str) -> str:
    try:
        from .web import _load_settings
        return str((_load_settings() or {}).get(key) or "").strip()
    except Exception:
        return ""


def _base() -> str:
    """Dodo's licence server: live, or test mode while trying Plus out (settings dodo_base)."""
    return (os.environ.get("HEARTH_DODO_BASE") or _setting("dodo_base") or LIVE).rstrip("/")


def checkout_url() -> str:
    return CHECKOUT_URL or _setting("plus_checkout")


def _file() -> str:
    return os.path.join(os.path.expanduser("~"), ".hearth", "plus.json")


def _read() -> Dict[str, Any]:
    try:
        with open(_file(), encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_file()), exist_ok=True)
    tmp = _file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _file())


def _post(path: str, body: Dict[str, Any], timeout: float = 15) -> Dict[str, Any]:
    """{'status': http code, 'json': body} ; status 0 when Dodo couldn't be reached."""
    req = urllib.request.Request(_base() + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Hearth"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return {"status": r.status, "json": json.loads(raw) if raw else {}}
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        return {"status": e.code, "json": data}
    except (urllib.error.URLError, OSError, ValueError):
        return {"status": 0, "json": {}}


def _device_name() -> str:
    return (platform.node() or "this PC")[:60]


_WHY = {403: "that key isn't active (the subscription may have ended)",
        404: "that key wasn't found; check it was copied whole",
        422: "that key is already active on as many PCs as it allows; deactivate it on one first"}


def activate(key: str) -> Dict[str, Any]:
    """Activate a key on this PC. {ok, error}."""
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "paste the licence key from your Plus receipt"}
    r = _post("/licenses/activate", {"license_key": key, "name": _device_name()})
    if r["status"] == 0:
        return {"ok": False, "error": "couldn't reach the licence server; check the internet and try again"}
    if r["status"] >= 400:
        return {"ok": False, "error": _WHY.get(r["status"]) or f"the licence server said no ({r['status']})"}
    instance = str((r["json"] or {}).get("id") or "")
    _write({"key": key, "instance": instance, "checked": time.time(), "valid": True})
    return {"ok": True}


def deactivate() -> Dict[str, Any]:
    """Free this PC's activation (to move Plus to another PC)."""
    d = _read()
    if d.get("key") and d.get("instance"):
        _post("/licenses/deactivate", {"license_key": d["key"], "license_key_instance_id": d["instance"]})
    try:
        os.remove(_file())
    except OSError:
        pass
    return {"ok": True}


def check(force: bool = False) -> Dict[str, Any]:
    """Ask Dodo whether the key still holds, at most once a day unless forced."""
    d = _read()
    if not d.get("key") or d.get("owner"):
        return d                # an owner key is the relay's to check, not Dodo's
    if not force and time.time() - float(d.get("checked") or 0) < CHECK_EVERY_S:
        return d
    body: Dict[str, Any] = {"license_key": d["key"]}
    if d.get("instance"):
        body["license_key_instance_id"] = d["instance"]
    r = _post("/licenses/validate", body)
    if r["status"] == 0:
        return d                                   # offline: the grace period decides
    d["valid"] = bool(r["status"] < 400 and (r["json"] or {}).get("valid"))
    d["checked"] = time.time()
    _write(d)
    return d


def status() -> Dict[str, Any]:
    """{plus: bool, key_hint, checked, offline_grace, checkout} for the app."""
    d = check()
    has = bool(d.get("key"))
    fresh = bool(d.get("owner")) or time.time() - float(d.get("checked") or 0) < GRACE_S
    on = has and bool(d.get("valid")) and fresh
    key = str(d.get("key") or "")
    return {"plus": on, "has_key": has, "key_hint": ("…" + key[-4:]) if key else "",
            "lapsed": has and not on, "checked": d.get("checked"), "checkout": checkout_url()}


def active() -> bool:
    return status()["plus"]
