"""A provider's limit, known until it resets, so a turn before then isn't spent finding out again.

An agent brain already skips a turn its plan is known to be out for
(claude_brain.used_up). For a provider on the person's own key the same: when a
quota or rate-limit answer carries a reset time (Retry-After, OpenAI's and
Groq's x-ratelimit-reset-requests/-tokens, OpenRouter's X-RateLimit-Reset,
Anthropic's anthropic-ratelimit-*-reset), it is kept per provider and key, and
the next turn before that time shows the card with the time instead of calling.
A call that goes through clears it. Kept in ~/.hearth/provider_limits.json, so
the window, the terminal and the chat-app bridges all know.

The key itself is never kept: only the first 12 hex of its SHA-256, enough to
tell two keys for one provider apart.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

PATH = os.path.join(os.path.expanduser("~"), ".hearth", "provider_limits.json")
MAX_HOLD_S = 8 * 86400          # a reset said to be further off than this is not believed
_LOCK = threading.Lock()
_SEEN: Dict[str, Any] = {"mtime": -1.0, "data": {}}


def _key(base: str, key: str) -> str:
    host = (urlparse(base or "").hostname or base or "").lower()
    fp = hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:12]
    return f"{host}|{fp}"


def _read() -> Dict[str, Any]:
    try:
        mt = os.path.getmtime(PATH)
    except OSError:
        _SEEN.update(mtime=-1.0, data={})
        return {}
    if mt != _SEEN["mtime"]:
        try:
            with open(PATH, encoding="utf-8") as f:
                d = json.load(f)
            _SEEN.update(mtime=mt, data=d if isinstance(d, dict) else {})
        except (OSError, ValueError):
            _SEEN.update(mtime=mt, data={})
    return dict(_SEEN["data"])


def _write(d: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, PATH)
    except OSError:
        pass


_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def _seconds(v: str, now: float) -> Optional[float]:
    """When a header value says the limit resets, as a time. Takes seconds ("20",
    "1.5"), a duration ("1m30s", "6ms", "2m59.56s"), an RFC 3339 or HTTP date, or a
    Unix time in seconds or milliseconds."""
    v = (v or "").strip()
    if not v:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", v):
        n = float(v)
        if n > 1e12:
            return n / 1000                   # Unix milliseconds (OpenRouter)
        if n > 1e9:
            return n                          # Unix seconds
        return now + n                        # seconds from now
    m = _DUR.match(v)
    if m and any(m.groups()):
        h, mi, s, ms = (float(x) if x else 0.0 for x in m.groups())
        return now + h * 3600 + mi * 60 + s + ms / 1000
    try:
        from datetime import datetime
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(v).timestamp()
    except (TypeError, ValueError):
        return None


_RESET_HEADERS = ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-ratelimit-reset",
                  "anthropic-ratelimit-requests-reset", "anthropic-ratelimit-tokens-reset",
                  "anthropic-ratelimit-input-tokens-reset", "anthropic-ratelimit-output-tokens-reset")


def reset_at(exc: Any, now: Optional[float] = None) -> Optional[float]:
    """The latest reset time the provider's answer names, or None."""
    now = time.time() if now is None else now
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    times = []
    for h in _RESET_HEADERS:
        try:
            v = headers.get(h) or headers.get(h.title())
        except Exception:
            v = None
        t = _seconds(str(v), now) if v else None
        if t:
            times.append(t)
    if not times:
        text = str(exc).lower()
        m = re.search(r"retry[_ ]?delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text) \
            or re.search(r"(?:try again|retry) in (\d+(?:\.\d+)?)\s*s(?:ec|econds?)?\b", text)
        if m:
            times.append(now + float(m.group(1)))
    times = [t for t in times if now < t <= now + MAX_HOLD_S]
    return max(times) if times else None


def note(base: str, key: str, category: str, said: str, exc: Any, now: Optional[float] = None) -> Optional[float]:
    """After a quota or rate-limit answer: keep when it resets, if the answer says.
    Returns that time, or None when nothing was kept."""
    if category not in ("quota", "rate_limit"):
        return None
    now = time.time() if now is None else now
    until = reset_at(exc, now)
    if not until:
        return None
    with _LOCK:
        d = _read()
        d = {k: v for k, v in d.items() if (v.get("until") or 0) > now}
        d[_key(base, key)] = {"until": until, "what": category, "said": (said or "")[:300]}
        _write(d)
    return until


def blocked(base: str, key: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The limit this provider and key are under right now, or None."""
    now = time.time() if now is None else now
    got = _read().get(_key(base, key))
    if got and (got.get("until") or 0) > now:
        return dict(got)
    return None


def clear(base: str, key: str) -> None:
    """A call went through: whatever was kept for this provider and key is over."""
    k = _key(base, key)
    if k not in _read():
        return
    with _LOCK:
        d = _read()
        d.pop(k, None)
        _write(d)


def card(provider: str, got: Dict[str, Any], now: Optional[float] = None) -> str:
    """What the person is told, the way agent brains say it: who, until when."""
    now = time.time() if now is None else now
    until = float(got.get("until") or now)
    left = until - now
    if left < 90:
        when = f"for {max(1, int(round(left)))} more seconds"
    else:
        from .clock import hm
        when = "until " + hm(until, day=time.localtime(until)[:3] != time.localtime(now)[:3])
    what = "is out of usage" if got.get("what") == "quota" else "is limiting requests"
    return f"{provider} {what} {when}. Switch to another brain to carry on now."
