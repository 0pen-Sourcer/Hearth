"""Web search backends, tried in order until one answers.

Hearth used to have exactly one backend, an HTML scrape of DuckDuckGo, which
DuckDuckGo blocks with a bot-check page whenever it feels like it. One blocked
vendor then meant no search at all.

The shape here: a ring of providers that need no account, walked from a rotating
start so a vendor that rate-limits this minute is simply skipped rather than
being the whole answer. A key in settings promotes a provider to the front. The
old scrape stays as the last resort.

Nothing here logs a query anywhere but the provider being asked.
"""
from __future__ import annotations

import json
from . import env as _env
import os
import random
import re
import threading
import urllib.error
import urllib.request
from typing import List, Tuple

_UA = "Hearth/0.7.6"
_TIMEOUT = 25

# Vendors reachable with no account. Walked from a rotating start so a rate limit
# on one is a skip, not a dead end.
_KEYLESS_RING: List[str] = ["parallel", "exa"]
_ring_lock = threading.Lock()
_ring_cursor = random.randrange(len(_KEYLESS_RING) or 1)


def _next_start() -> int:
    """Advance the ring cursor one step and return where this call should begin."""
    global _ring_cursor
    with _ring_lock:
        start = _ring_cursor
        _ring_cursor = (_ring_cursor + 1) % len(_KEYLESS_RING)
    return start


def _settings() -> dict:
    try:
        base = _env.get("WORKSPACE") or os.path.expanduser("~/Jarvis")
        with open(os.path.join(base, "settings.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _mcp_call(url: str, tool: str, args: dict) -> Tuple[str, bool]:
    """One MCP tools/call over plain HTTP. Returns (text, rate_limited).

    These endpoints answer either as JSON or as a single server-sent event, so
    the object is pulled out of whatever wrapper comes back."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        raw = r.read().decode("utf-8", "replace")
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    result = data.get("result") or {}
    limited = bool((result.get("_meta") or {}).get("ai.exa/rateLimited"))
    text = "".join(c.get("text", "") for c in (result.get("content") or [])
                   if c.get("type") == "text")
    if result.get("isError"):
        return "", limited
    return text.strip(), limited


def _p_parallel(query: str, limit: int) -> str:
    text, limited = _mcp_call(
        "https://search.parallel.ai/mcp", "web_search",
        {"objective": query, "search_queries": [query]})
    if limited or not text:
        return ""
    return _format_parallel(text, limit)


def _format_parallel(text: str, limit: int) -> str:
    """Parallel answers with a JSON document; render the rows Hearth's way."""
    try:
        doc = json.loads(text)
    except Exception:
        return text[:4000]
    out = []
    for row in (doc.get("results") or [])[:limit]:
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        snippet = " ".join(str(x) for x in (row.get("excerpts") or [])[:2])
        snippet = re.sub(r"\s+", " ", snippet).strip()[:400]
        out.append(f"- {title}\n  {url}\n  {snippet}")
    return "\n\n".join(out)


def _p_exa(query: str, limit: int) -> str:
    text, limited = _mcp_call(
        "https://mcp.exa.ai/mcp", "web_search_exa",
        {"query": query, "numResults": limit})
    if limited or not text:
        return ""
    return text[:6000]


_KEYLESS: dict = {"parallel": _p_parallel, "exa": _p_exa}


def search(query: str, limit: int = 6) -> Tuple[str, str]:
    """Return (results, provider). Empty results means every backend declined.

    Order: a configured provider first when one is set, then the keyless ring
    from a rotating start, then the caller's own fallback."""
    query = (query or "").strip()
    if not query:
        return "", ""
    preferred = str(_settings().get("web_search_provider") or "").strip().lower()
    order: List[str] = []
    if preferred in _KEYLESS:
        order.append(preferred)
    start = _next_start()
    for i in range(len(_KEYLESS_RING)):
        name = _KEYLESS_RING[(start + i) % len(_KEYLESS_RING)]
        if name not in order:
            order.append(name)
    for name in order:
        fn = _KEYLESS.get(name)
        if not fn:
            continue
        try:
            text = fn(query, limit)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            continue
        except Exception:
            continue
        if text and text.strip():
            return text.strip(), name
    return "", ""


def provider_status() -> List[dict]:
    """What each backend reports right now. Used by Settings, never by a turn."""
    rows = []
    for name in _KEYLESS_RING:
        try:
            text = _KEYLESS[name]("hearth local ai", 1)
            rows.append({"name": name, "ok": bool(text), "keyless": True,
                         "note": "" if text else "no answer or rate limited"})
        except Exception as e:
            rows.append({"name": name, "ok": False, "keyless": True,
                         "note": f"{type(e).__name__}: {e}"})
    return rows
