"""The opt-in install count: once a day, a few facts with no names and no content.

Off unless the person said yes in setup or Settings (install_count). What is sent is
exactly payload(): a random id made for this install, the version, the OS and the kind
of brain (local, cloud or agent). It goes to the relay's /count, which keeps one row
per install per day and no IP addresses.
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
import urllib.request
import uuid
from typing import Any, Dict

EVERY_S = 86400
_ID_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "install_id")
_LAST_FILE = os.path.join(os.path.expanduser("~"), ".hearth", "install_count_last")


def on() -> bool:
    try:
        from .web import _load_settings
        return _load_settings().get("install_count") is True
    except Exception:
        return False


def install_id(make: bool = False) -> str:
    try:
        with open(_ID_FILE, encoding="utf-8") as f:
            got = f.read().strip()
        if got:
            return got
    except OSError:
        pass
    if not make:
        return ""
    new = uuid.uuid4().hex
    os.makedirs(os.path.dirname(_ID_FILE), exist_ok=True)
    with open(_ID_FILE, "w", encoding="utf-8") as f:
        f.write(new)
    return new


def brain_kind() -> str:
    try:
        from . import claude_brain as cb
        if cb.current():
            return "agent"
        from . import headless as hl
        return "local" if hl.provider_for(hl.LOCAL_API_BASE) == "local" else "cloud"
    except Exception:
        return "local"


def payload(make_id: bool = False) -> Dict[str, Any]:
    """Everything that is sent, as it is sent. The id shows as "(made when you say yes)"
    until there is one."""
    from .updater import HEARTH_VERSION
    return {"id": install_id(make_id) or "(made when you say yes)", "v": HEARTH_VERSION,
            "os": platform.system() or "unknown", "brain": brain_kind()}


def _last() -> float:
    try:
        return os.path.getmtime(_LAST_FILE)
    except OSError:
        return 0.0


def ping_if_due(now: float = 0.0) -> bool:
    """Send today's count when it is on and a day has passed. True when sent."""
    now = now or time.time()
    if not on() or now - _last() < EVERY_S - 600:
        return False
    from .relay_bridge import relay_url
    body = json.dumps(payload(make_id=True)).encode()
    req = urllib.request.Request(relay_url() + "/count", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status == 200
    except Exception:
        return False
    if ok:
        os.makedirs(os.path.dirname(_LAST_FILE), exist_ok=True)
        with open(_LAST_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(now)))
    return ok


def start() -> None:
    """Look once an hour; send at most once a day, only when on."""
    def loop():
        while True:
            try:
                ping_if_due()
            except Exception:
                pass
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="install-count").start()
